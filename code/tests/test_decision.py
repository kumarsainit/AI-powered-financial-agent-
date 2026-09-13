from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.decision import (
    AffordabilityStatus,
    CandidateKind,
    ChangeAction,
    RecommendedMethod,
    RejectionReason,
)
from buyorwait.decision_invariants import DecisionInvariantViolation, check_recommendation_invariants
from buyorwait.domain import (
    Direction,
    EventStatus,
    EventType,
    ExchangeRateRecord,
    Flexibility,
    PaymentMethod,
    PaymentOption,
    Request,
    RequestType,
)
from buyorwait.financial_state import ConversionStatus
from buyorwait.forecast import build_financial_state
from buyorwait.planning import (
    DecisionConfig,
    build_recommendation,
    compute_safe_amount,
    earliest_safe_full_payment_date,
    normalize_request,
    normalize_request_in_currency,
    simulate_plan,
)
from tests.test_forecast import (
    HOME,
    REQUEST_DATE,
    bundle,
    evidence_bundle,
    event,
    fact,
    monthly_series,
    profile,
    rates,
    request,
)


def option(
    option_id: str,
    method: PaymentMethod,
    amount: str,
    count: int,
    first: date,
    frequency: int | None = 30,
    fee: str = "0",
    total: str | None = None,
) -> PaymentOption:
    return PaymentOption(
        payment_option_id=option_id,
        request_id="request_x",
        payment_method=method,
        payment_amount=Decimal(amount),
        number_of_payments=count,
        first_payment_date=first,
        payment_frequency_days=frequency,
        financing_fee=Decimal(fee),
        total_payable_amount=Decimal(total if total is not None else str(Decimal(amount) * count)),
    )


def scenario(
    events=(),
    balance="10000",
    minimum="1000",
    requested="5000",
    options=(),
    accepted=(PaymentMethod.FULL_PAYMENT,),
    allows_partial=False,
    max_installments=None,
    due_days=60,
    reduce=("streaming",),
    stop=("streaming",),
    protect=("rent", "groceries"),
    evidence=None,
    rate_table=None,
    config=DecisionConfig(),
):
    user_profile = profile(
        balance=balance, minimum=minimum, protect=protect, reduce=reduce, stop=stop
    )
    user_profile = user_profile.__class__(
        **{
            **user_profile.__dict__,
            "payment_methods_user_will_consider": accepted,
            "max_installment_months": max_installments,
        }
    )
    base_request = request()
    purchase_request = Request(
        request_id=base_request.request_id,
        user_id=base_request.user_id,
        request_date=REQUEST_DATE,
        request_type=RequestType.PURCHASE,
        requested_amount=Decimal(requested),
        desired_completion_date=REQUEST_DATE + timedelta(days=due_days),
        allows_partial_payment=allows_partial,
        request_text="test request",
    )
    request_bundle = bundle(
        events, user_profile=user_profile, request_obj=purchase_request, rate_table=rate_table
    )
    request_bundle = request_bundle.__class__(
        **{**request_bundle.__dict__, "payment_options": tuple(options)}
    )
    forecast = build_financial_state(request_bundle, evidence)
    spec = normalize_request(request_bundle, forecast)
    recommendation = build_recommendation(request_bundle, forecast, config, spec)
    check_recommendation_invariants(recommendation, forecast, spec)
    return request_bundle, forecast, spec, recommendation


FULL_TODAY = option("payment_option_01", PaymentMethod.FULL_PAYMENT, "5000", 1, REQUEST_DATE, None)


def test_affordable_full_payment_today():
    _b, _f, _s, rec = scenario(options=(FULL_TODAY,))
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_NOW
    assert rec.recommended_payment_method is RecommendedMethod.FULL_PAYMENT
    assert rec.payment_plan == ((REQUEST_DATE, Decimal("5000")),)
    assert rec.earliest_date_for_full_payment == REQUEST_DATE
    assert rec.amount_safe_to_pay == Decimal("5000")


def test_todays_balance_is_never_sufficient_on_its_own():
    future = (event("big", REQUEST_DATE + timedelta(days=20), "6000", status=EventStatus.SCHEDULED, category="rent"),)
    _b, _f, _s, rec = scenario(events=future, options=(FULL_TODAY,))
    assert rec.affordability_status is not AffordabilityStatus.AFFORDABLE_NOW
    assert rec.amount_safe_to_pay < Decimal("5000")


def test_future_salary_makes_a_later_purchase_safe():
    events = (
        event("small", REQUEST_DATE + timedelta(days=1), "6000", status=EventStatus.SCHEDULED, category="rent"),
        event(
            "pay",
            REQUEST_DATE + timedelta(days=10),
            "9000",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            status=EventStatus.SCHEDULED,
            category="salary",
        ),
    )
    _b, _f, _s, rec = scenario(events=events, options=(FULL_TODAY,))
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_LATER
    assert rec.recommended_payment_method is RecommendedMethod.WAIT
    assert rec.earliest_date_for_full_payment == REQUEST_DATE + timedelta(days=10)
    assert rec.payment_plan == ((REQUEST_DATE + timedelta(days=10), Decimal("5000")),)


def test_full_payment_later_is_validated_by_the_simulator():
    events = (
        event("cost", REQUEST_DATE + timedelta(days=2), "6000", status=EventStatus.SCHEDULED, category="rent"),
        event(
            "pay",
            REQUEST_DATE + timedelta(days=5),
            "9000",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            status=EventStatus.SCHEDULED,
            category="salary",
        ),
    )
    _b, forecast, spec, rec = scenario(events=events, options=(FULL_TODAY,))
    outcome = simulate_plan(forecast, spec, rec.payment_plan)
    assert not outcome.breaches_minimum


def test_installment_plan_is_safe_and_selected_when_full_payment_is_not_accepted():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1000", 5, REQUEST_DATE, 10, "100", "5100")
    _b, _f, _s, rec = scenario(
        options=(FULL_TODAY, installments),
        accepted=(PaymentMethod.INSTALLMENTS,),
        max_installments=6,
    )
    assert rec.recommended_payment_method is RecommendedMethod.INSTALLMENTS
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_WITH_PLAN
    assert len(rec.payment_plan) == 5


def test_installment_affordable_upfront_but_unsafe_later_is_rejected():
    drain = (
        event("bill", REQUEST_DATE + timedelta(days=25), "8500", status=EventStatus.SCHEDULED, category="rent"),
    )
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1000", 4, REQUEST_DATE, 15, "0", "4000")
    _b, _f, _s, rec = scenario(
        events=drain,
        options=(installments,),
        accepted=(PaymentMethod.INSTALLMENTS,),
        max_installments=6,
    )
    evaluation = next(e for e in rec.candidates if e.candidate.kind is CandidateKind.INSTALLMENTS)
    assert RejectionReason.INSTALLMENT_UNSAFE in evaluation.rejection_reasons
    assert rec.recommended_payment_method is not RecommendedMethod.INSTALLMENTS


def test_partial_payment_splits_by_subtraction():
    events = (
        event("cost", REQUEST_DATE + timedelta(days=1), "6500", status=EventStatus.SCHEDULED, category="rent"),
        event(
            "pay",
            REQUEST_DATE + timedelta(days=20),
            "9000",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            status=EventStatus.SCHEDULED,
            category="salary",
        ),
    )
    _b, _f, spec, rec = scenario(
        events=events,
        options=(FULL_TODAY,),
        accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.PARTIAL_PAYMENT),
        allows_partial=True,
    )
    partial = next(
        (e for e in rec.candidates if e.candidate.kind is CandidateKind.PARTIAL_PAYMENT), None
    )
    assert partial is not None
    first, second = partial.candidate.payments
    assert first[1] + second[1] == spec.requested_amount_home
    assert second[1] == spec.requested_amount_home - first[1]
    assert first[0] == REQUEST_DATE


def test_payment_method_ineligible_when_term_exceeds_user_maximum():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "500", 12, REQUEST_DATE, 30, "1000", "6000")
    _b, _f, _s, rec = scenario(
        options=(installments,),
        accepted=(PaymentMethod.INSTALLMENTS,),
        max_installments=6,
    )
    evaluation = next(e for e in rec.candidates if e.candidate.kind is CandidateKind.INSTALLMENTS)
    assert RejectionReason.INSTALLMENT_TERM_TOO_LONG in evaluation.rejection_reasons
    assert not evaluation.gates.method_eligibility
    assert evaluation.gates.financial_capacity


def test_preference_mismatch_blocks_a_financially_safe_plan():
    _b, _f, _s, rec = scenario(options=(FULL_TODAY,), accepted=(PaymentMethod.INSTALLMENTS,))
    evaluation = next(e for e in rec.candidates if e.candidate.kind is CandidateKind.FULL_PAYMENT_TODAY)
    assert evaluation.gates.financial_capacity
    assert not evaluation.gates.user_preference
    assert RejectionReason.PAYMENT_METHOD_NOT_PREFERRED in evaluation.rejection_reasons
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


def test_minimum_balance_violation_is_reported_as_a_blocking_reason():
    _b, _f, _s, rec = scenario(balance="5200", minimum="1000", requested="5000", options=(FULL_TODAY,))
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert RejectionReason.MINIMUM_BALANCE_VIOLATION in rec.blocking_reasons


def test_flexible_spending_reduction_makes_a_purchase_safe():
    streaming = monthly_series(
        "s", "streaming", ["600", "600", "600"], flexibility=Flexibility.STOPPABLE
    )
    _b, _f, _s, rec = scenario(
        events=streaming,
        balance="6300",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
    )
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_WITH_PLAN
    assert rec.recommended_payment_method is RecommendedMethod.FULL_PAYMENT
    assert [c.action for c in rec.spending_changes] == [ChangeAction.STOP]
    assert rec.spending_changes[0].category == "streaming"


def test_reducible_expense_is_never_reduced_below_its_minimum():
    streaming = monthly_series(
        "s",
        "streaming",
        ["600", "600", "600"],
        flexibility=Flexibility.REDUCIBLE,
        minimum_allowed="300",
    )
    _b, _f, _s, rec = scenario(
        events=streaming,
        balance="6600",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
        reduce=("streaming",),
        stop=(),
    )
    for change in rec.spending_changes:
        assert change.action is ChangeAction.REDUCE_TO
        assert change.new_amount == Decimal("300")
        assert change.new_amount >= change.minimum_allowed_amount


def test_stoppable_expense_is_stopped_when_permitted():
    streaming = monthly_series("s", "streaming", ["900", "900", "900"], flexibility=Flexibility.STOPPABLE)
    _b, _f, _s, rec = scenario(
        events=streaming, balance="6500", minimum="1000", requested="5000", options=(FULL_TODAY,)
    )
    assert rec.spending_changes
    assert rec.spending_changes[0].new_amount == Decimal("0")


def test_protected_category_is_never_changed():
    groceries = monthly_series("g", "groceries", ["900", "900", "900"], flexibility=Flexibility.REDUCIBLE, minimum_allowed="100")
    _b, _f, _s, rec = scenario(
        events=groceries,
        balance="6500",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
        reduce=("groceries",),
        protect=("groceries",),
    )
    assert not rec.spending_changes


def test_minimal_disruption_is_preferred_among_several_possible_changes():
    small = monthly_series("s", "streaming", ["800", "800", "800"], flexibility=Flexibility.STOPPABLE)
    large = monthly_series("g", "gym", ["1500", "1500", "1500"], flexibility=Flexibility.STOPPABLE)
    _b, _f, _s, rec = scenario(
        events=small + large,
        balance="10500",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
        reduce=("streaming", "gym"),
        stop=("streaming", "gym"),
    )
    assert len(rec.spending_changes) == 1
    assert rec.spending_changes[0].category == "streaming"


def test_unresolved_future_obligation_is_surfaced_without_inventing_an_amount():
    evidence = evidence_bundle(
        fact(
            __import__("buyorwait.evidence", fromlist=["FactType"]).FactType.EXPENSE_NEW_RECURRING,
            amount=None,
            effective=REQUEST_DATE + timedelta(days=10),
            category="childcare",
            recurrence="monthly",
        )
    )
    _b, forecast, _s, rec = scenario(options=(FULL_TODAY,), evidence=evidence)
    assert rec.explanation_facts.blocking_obligations
    assert RejectionReason.BLOCKING_UNRESOLVED_OBLIGATION in rec.blocking_reasons
    assert all(o.description for o in rec.explanation_facts.blocking_obligations)


def test_unresolved_obligation_can_block_every_plan_when_configured():
    evidence = evidence_bundle(
        fact(
            __import__("buyorwait.evidence", fromlist=["FactType"]).FactType.EXPENSE_NEW_RECURRING,
            amount=None,
            effective=REQUEST_DATE + timedelta(days=10),
            category="childcare",
            recurrence="monthly",
        )
    )
    _b, _f, _s, rec = scenario(
        options=(FULL_TODAY,),
        evidence=evidence,
        config=DecisionConfig(block_on_unresolved_obligations=True),
    )
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


def test_speculative_income_never_supports_a_plan():
    FactType = __import__("buyorwait.evidence", fromlist=["FactType"]).FactType
    evidence = evidence_bundle(
        fact(FactType.INCOME_BONUS_PENDING, amount="50000", effective=REQUEST_DATE + timedelta(days=5))
    )
    _b, _f, _s, rec = scenario(
        balance="5200", minimum="1000", requested="5000", options=(FULL_TODAY,), evidence=evidence
    )
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


def test_confirmed_income_supports_a_later_plan():
    FactType = __import__("buyorwait.evidence", fromlist=["FactType"]).FactType
    evidence = evidence_bundle(
        fact(FactType.SALARY_CONFIRMED, amount="9000", effective=REQUEST_DATE + timedelta(days=7), recurrence="once")
    )
    _b, _f, _s, rec = scenario(
        balance="5200", minimum="1000", requested="5000", options=(FULL_TODAY,), evidence=evidence
    )
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_LATER
    assert rec.earliest_date_for_full_payment == REQUEST_DATE + timedelta(days=7)


def test_pending_debit_reservation_reduces_the_safe_amount_exactly_once():
    events = (event("p1", REQUEST_DATE - timedelta(days=2), "500", status=EventStatus.PENDING, category="shopping"),)
    _b, forecast, _s, rec = scenario(events=events, options=(FULL_TODAY,))
    assert forecast.starting_state.reserved_total == Decimal("500")
    assert rec.amount_safe_to_pay == Decimal("5000")
    assert rec.explanation_facts.available_cash_at_request_date == Decimal("9500")


def test_unrealized_investment_value_is_not_treated_as_cash():
    events = (
        event("buy", REQUEST_DATE - timedelta(days=30), "1000", event_type=EventType.INVESTMENT_PURCHASE, category="investment"),
        event(
            "val",
            REQUEST_DATE - timedelta(days=5),
            "9000",
            event_type=EventType.INVESTMENT_VALUATION,
            direction=Direction.NON_CASH,
            status=EventStatus.UNREALIZED,
            category="investment",
            linked="buy",
        ),
    )
    _b, _f, _s, rec = scenario(events=events, balance="5200", minimum="1000", requested="5000", options=(FULL_TODAY,))
    assert rec.explanation_facts.unrealized_investment_value == Decimal("9000")
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


def test_currency_conversion_of_the_requested_amount():
    table = rates((ExchangeRateRecord(REQUEST_DATE, "USD", HOME, Decimal("2")),))
    request_bundle, forecast, _s, _rec = scenario(options=(FULL_TODAY,), rate_table=table)
    spec = normalize_request_in_currency(request_bundle, forecast, "USD")
    assert spec.requested_amount_home == Decimal("10000")
    assert spec.conversion_status is ConversionStatus.CONVERTED


def test_missing_exchange_rate_blocks_every_candidate():
    request_bundle, forecast, _s, _rec = scenario(options=(FULL_TODAY,))
    spec = normalize_request_in_currency(request_bundle, forecast, "USD")
    assert spec.requested_amount_home is None
    recommendation = build_recommendation(request_bundle, forecast, DecisionConfig(), spec)
    assert recommendation.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert recommendation.amount_safe_to_pay == Decimal("0")


def test_safe_amount_boundary_is_exact():
    events = (event("bill", REQUEST_DATE + timedelta(days=10), "3000", status=EventStatus.SCHEDULED, category="rent"),)
    _b, forecast, spec, rec = scenario(events=events, requested="100000", options=(FULL_TODAY,))
    amount = rec.amount_safe_to_pay
    assert amount == Decimal("6000")
    assert not simulate_plan(forecast, spec, ((REQUEST_DATE, amount),)).breaches_minimum
    assert simulate_plan(forecast, spec, ((REQUEST_DATE, amount + Decimal("0.01")),)).breaches_minimum


def test_safe_amount_is_capped_by_the_requested_amount():
    _b, _f, _s, rec = scenario(requested="500", options=(option("payment_option_01", PaymentMethod.FULL_PAYMENT, "500", 1, REQUEST_DATE, None),))
    assert rec.amount_safe_to_pay == Decimal("500")


def test_no_safe_full_payment_date_within_the_horizon():
    _b, forecast, _s, rec = scenario(balance="2000", minimum="1000", requested="5000", options=(FULL_TODAY,))
    assert rec.earliest_date_for_full_payment is None
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert earliest_safe_full_payment_date(forecast, Decimal("5000")) is None


def test_lower_total_cost_plan_wins_between_two_safe_plans():
    cheap = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    expensive = option("payment_option_03", PaymentMethod.INSTALLMENTS, "1100", 5, REQUEST_DATE, 10, "500", "5500")
    _b, _f, _s, rec = scenario(
        options=(cheap, expensive),
        accepted=(PaymentMethod.INSTALLMENTS,),
        max_installments=6,
    )
    assert rec.selected_candidate_id == "installments:payment_option_02"


def test_full_payment_beats_installments_when_both_are_safe_and_accepted():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1100", 5, REQUEST_DATE, 10, "500", "5500")
    _b, _f, _s, rec = scenario(
        options=(FULL_TODAY, installments),
        accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.INSTALLMENTS),
        max_installments=6,
    )
    assert rec.recommended_payment_method is RecommendedMethod.FULL_PAYMENT


def test_payment_on_the_same_day_as_income_uses_that_income():
    events = (
        event("bill", REQUEST_DATE, "8500", status=EventStatus.SCHEDULED, category="rent"),
        event(
            "pay",
            REQUEST_DATE + timedelta(days=6),
            "9000",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            status=EventStatus.SCHEDULED,
            category="salary",
        ),
    )
    _b, _f, _s, rec = scenario(events=events, requested="5000", options=(FULL_TODAY,))
    assert rec.earliest_date_for_full_payment == REQUEST_DATE + timedelta(days=6)


def test_ninety_day_boundary_limits_the_earliest_safe_date():
    events = (
        event("bill", REQUEST_DATE, "9500", status=EventStatus.SCHEDULED, category="rent"),
        event(
            "pay",
            REQUEST_DATE + timedelta(days=95),
            "9000",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            status=EventStatus.SCHEDULED,
            category="salary",
        ),
    )
    _b, _f, _s, rec = scenario(events=events, requested="5000", options=(FULL_TODAY,))
    assert rec.earliest_date_for_full_payment is None
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


def test_candidate_ordering_is_deterministic():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    first = scenario(options=(FULL_TODAY, installments), accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.INSTALLMENTS), max_installments=6)[3]
    second = scenario(options=(FULL_TODAY, installments), accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.INSTALLMENTS), max_installments=6)[3]
    assert [e.candidate.candidate_id for e in first.candidates] == [
        e.candidate.candidate_id for e in second.candidates
    ]
    assert first.selected_candidate_id == second.selected_candidate_id
    assert first.amount_safe_to_pay == second.amount_safe_to_pay


def test_every_candidate_is_validated_through_the_phase_four_simulator():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    _b, forecast, spec, rec = scenario(
        options=(FULL_TODAY, installments),
        accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.INSTALLMENTS),
        max_installments=6,
    )
    for evaluation in rec.candidates:
        outcome = simulate_plan(
            forecast, spec, evaluation.candidate.payments, evaluation.candidate.spending_changes
        )
        assert outcome.minimum_projected_balance == evaluation.minimum_projected_balance
        assert outcome.breaches_minimum == evaluation.breaches_minimum


def test_safe_amount_is_not_a_current_balance_shortcut():
    events = (event("bill", REQUEST_DATE + timedelta(days=30), "4000", status=EventStatus.SCHEDULED, category="rent"),)
    _b, forecast, _s, rec = scenario(events=events, requested="100000", options=(FULL_TODAY,))
    naive = forecast.starting_state.available_cash - forecast.starting_state.minimum_balance_to_keep
    assert rec.amount_safe_to_pay == Decimal("5000")
    assert rec.amount_safe_to_pay < naive


def test_explanation_facts_are_populated():
    _b, _f, _s, rec = scenario(options=(FULL_TODAY,))
    facts = rec.explanation_facts
    assert facts.available_cash_at_request_date == Decimal("10000")
    assert facts.minimum_required_balance == Decimal("1000")
    assert facts.purchase_amount_home == Decimal("5000")
    assert facts.selected_plan_total_cost == Decimal("5000")
    assert facts.earliest_safe_full_payment_date == REQUEST_DATE


def test_invariants_reject_a_plan_that_breaches_the_minimum_balance():
    request_bundle, forecast, spec, rec = scenario(options=(FULL_TODAY,))
    broken_candidate = rec.candidates[0].candidate.__class__(
        **{**rec.candidates[0].candidate.__dict__, "payments": ((REQUEST_DATE, Decimal("99999")),)}
    )
    broken_evaluation = rec.candidates[0].__class__(
        **{**rec.candidates[0].__dict__, "candidate": broken_candidate}
    )
    broken = rec.__class__(
        **{
            **rec.__dict__,
            "candidates": (broken_evaluation,),
            "selected_candidate_id": broken_candidate.candidate_id,
            "payment_plan": broken_candidate.payments,
        }
    )
    with pytest.raises(DecisionInvariantViolation):
        check_recommendation_invariants(broken, forecast, spec)
