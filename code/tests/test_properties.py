from __future__ import annotations

import random
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.decision import AffordabilityStatus, CandidateKind, ChangeAction, RecommendedMethod
from buyorwait.domain import Direction, EventStatus, EventType, Flexibility, PaymentMethod
from buyorwait.planning import compute_safe_amount, simulate_plan
from tests.test_decision import option, scenario
from tests.test_forecast import REQUEST_DATE, event, monthly_series

CATEGORIES = ("groceries", "transport", "utilities", "rent", "dining")
FLEXIBLE_CATEGORIES = ("streaming", "gym", "hobby")
CENT = Decimal("0.01")


def synthetic_case(seed: int):
    rng = random.Random(seed)
    events = []
    for index, category in enumerate(rng.sample(CATEGORIES, rng.randint(1, 4))):
        amounts = [str(rng.randint(50, 900)) for _ in range(rng.randint(2, 4))]
        events.extend(monthly_series(f"e{index}", category, amounts))
    if rng.random() < 0.7:
        amounts = [str(rng.randint(1000, 4000)) for _ in range(rng.randint(2, 4))]
        events.extend(
            monthly_series(
                "inc",
                "salary",
                amounts,
                event_type=EventType.INCOME,
                direction=Direction.CREDIT,
            )
        )
    if rng.random() < 0.5:
        index = rng.randint(0, len(FLEXIBLE_CATEGORIES) - 1)
        category = FLEXIBLE_CATEGORIES[index]
        current = rng.randint(100, 500)
        events.extend(
            monthly_series(
                f"f{index}",
                category,
                [str(current)] * 3,
                flexibility=Flexibility.REDUCIBLE_OR_STOPPABLE,
                minimum_allowed=str(current // 2),
            )
        )
    if rng.random() < 0.3:
        events.append(
            event(
                "pending",
                REQUEST_DATE - timedelta(days=rng.randint(1, 5)),
                str(rng.randint(10, 400)),
                status=EventStatus.PENDING,
                category="shopping",
            )
        )

    balance = str(rng.randint(500, 20000))
    minimum = str(rng.randint(100, 3000))
    requested = str(rng.randint(100, 12000))
    accepted = tuple(
        rng.sample(
            [PaymentMethod.FULL_PAYMENT, PaymentMethod.PARTIAL_PAYMENT, PaymentMethod.INSTALLMENTS],
            rng.randint(1, 3),
        )
    )
    options = [option("payment_option_01", PaymentMethod.FULL_PAYMENT, requested, 1, REQUEST_DATE, None)]
    if rng.random() < 0.8:
        count = rng.randint(2, 4)
        per = (Decimal(requested) / count).quantize(CENT, rounding="ROUND_UP")
        options.append(
            option(
                "payment_option_02",
                PaymentMethod.INSTALLMENTS,
                str(per),
                count,
                REQUEST_DATE + timedelta(days=rng.randint(0, 10)),
                rng.choice([10, 20, 30]),
                "0",
                str(per * count),
            )
        )
    return dict(
        events=tuple(events),
        balance=balance,
        minimum=minimum,
        requested=requested,
        options=tuple(options),
        accepted=accepted,
        allows_partial=rng.random() < 0.5,
        max_installments=rng.choice([None, 2, 3, 6, 12]),
        due_days=rng.randint(10, 90),
        reduce=FLEXIBLE_CATEGORIES,
        stop=FLEXIBLE_CATEGORIES,
    )


SEEDS = list(range(60))


@pytest.mark.parametrize("seed", SEEDS)
def test_core_invariants_hold_for_random_synthetic_histories(seed):
    case = synthetic_case(seed)
    _bundle, forecast, spec, rec = scenario(**case)
    requested = spec.requested_amount_home

    assert rec.amount_safe_to_pay >= Decimal("0")
    assert rec.amount_safe_to_pay <= requested
    assert rec.amount_safe_to_pay.as_tuple().exponent >= -2

    if rec.amount_safe_to_pay > 0:
        assert not simulate_plan(forecast, spec, ((spec.request_date, rec.amount_safe_to_pay),)).breaches_minimum
    if rec.amount_safe_to_pay < requested:
        assert simulate_plan(
            forecast, spec, ((spec.request_date, rec.amount_safe_to_pay + CENT),)
        ).breaches_minimum

    if rec.earliest_date_for_full_payment is not None:
        assert forecast.window.contains(rec.earliest_date_for_full_payment)
        assert not simulate_plan(
            forecast, spec, ((rec.earliest_date_for_full_payment, requested),)
        ).breaches_minimum

    selected = next(
        (e for e in rec.candidates if e.candidate.candidate_id == rec.selected_candidate_id), None
    )
    if rec.recommended_payment_method is RecommendedMethod.NOT_RECOMMENDED:
        assert selected is None
        assert not rec.payment_plan
    else:
        assert selected is not None
        outcome = simulate_plan(forecast, spec, selected.candidate.payments, selected.candidate.spending_changes)
        assert not outcome.breaches_minimum
        assert selected.gates.passes


@pytest.mark.parametrize("seed", SEEDS)
def test_plan_shape_invariants_hold_for_random_synthetic_histories(seed):
    case = synthetic_case(seed)
    _bundle, _forecast, spec, rec = scenario(**case)
    selected = next(
        (e for e in rec.candidates if e.candidate.candidate_id == rec.selected_candidate_id), None
    )
    if selected is None:
        return
    candidate = selected.candidate

    if candidate.kind is CandidateKind.PARTIAL_PAYMENT:
        assert len(candidate.payments) == 2
        first, second = candidate.payments
        assert first[1] + second[1] == spec.requested_amount_home
        assert second[1] == spec.requested_amount_home - first[1]
        assert first[0] == spec.request_date
        assert spec.allows_partial_payment
        assert PaymentMethod.PARTIAL_PAYMENT in spec.accepted_methods

    if candidate.kind is CandidateKind.INSTALLMENTS:
        supplied = {o.payment_option_id: o for o in case["options"]}
        matched = supplied[candidate.payment_option_id]
        expected = tuple(
            (
                matched.first_payment_date + timedelta(days=(matched.payment_frequency_days or 0) * index),
                matched.payment_amount,
            )
            for index in range(matched.number_of_payments)
        )
        assert candidate.payments == expected
        assert spec.max_installment_months is not None
        assert matched.number_of_payments <= spec.max_installment_months
        assert candidate.completion_date <= spec.desired_completion_date

    if candidate.kind is CandidateKind.FULL_PAYMENT_LATER:
        assert len(candidate.payments) == 1
        assert candidate.payments[0][0] == rec.earliest_date_for_full_payment
        assert PaymentMethod.FULL_PAYMENT in spec.accepted_methods


@pytest.mark.parametrize("seed", SEEDS)
def test_spending_change_invariants_hold_for_random_synthetic_histories(seed):
    case = synthetic_case(seed)
    request_bundle, _forecast, _spec, rec = scenario(**case)
    profile = request_bundle.profile
    by_event = {e.event_id: e for e in request_bundle.events}

    assert len(rec.spending_changes) <= 3
    assert len({c.event_id for c in rec.spending_changes}) == len(rec.spending_changes)
    for change in rec.spending_changes:
        source = by_event[change.event_id]
        assert source.category not in profile.expense_categories_to_protect
        assert source.flexibility is not Flexibility.FIXED
        assert change.new_amount <= change.current_amount
        if change.action is ChangeAction.REDUCE_TO:
            assert change.minimum_allowed_amount is not None
            assert change.new_amount >= change.minimum_allowed_amount
            assert source.category in profile.expense_categories_user_is_willing_to_reduce
        else:
            assert change.new_amount == Decimal("0")
            assert source.category in profile.expense_categories_user_is_willing_to_stop


@pytest.mark.parametrize("seed", SEEDS)
def test_status_and_method_are_mutually_consistent(seed):
    case = synthetic_case(seed)
    _bundle, _forecast, spec, rec = scenario(**case)
    status = rec.affordability_status
    method = rec.recommended_payment_method

    if status is AffordabilityStatus.AFFORDABLE_NOW:
        assert method is RecommendedMethod.FULL_PAYMENT
        assert rec.earliest_date_for_full_payment == spec.request_date
        assert rec.amount_safe_to_pay == spec.requested_amount_home
        assert not rec.spending_changes
    elif status is AffordabilityStatus.AFFORDABLE_LATER:
        assert method is RecommendedMethod.WAIT
        assert rec.earliest_date_for_full_payment is not None
        assert rec.earliest_date_for_full_payment > spec.request_date
    elif status is AffordabilityStatus.AFFORDABLE_WITH_PLAN:
        assert method in (
            RecommendedMethod.FULL_PAYMENT,
            RecommendedMethod.PARTIAL_PAYMENT,
            RecommendedMethod.INSTALLMENTS,
        )
        if method is RecommendedMethod.FULL_PAYMENT:
            assert rec.spending_changes
    else:
        assert method is RecommendedMethod.NOT_RECOMMENDED
        assert not rec.payment_plan
        assert not rec.spending_changes


@pytest.mark.parametrize("seed", SEEDS[:30])
def test_safe_amount_never_depends_on_the_requested_amount_beyond_the_cap(seed):
    case = synthetic_case(seed)
    forecast = scenario(**case)[1]
    uncapped = compute_safe_amount(forecast, Decimal("10") ** 12)
    for requested in ("1", "100", "100000"):
        capped = compute_safe_amount(forecast, Decimal(requested))
        assert capped == min(uncapped, Decimal(requested))
