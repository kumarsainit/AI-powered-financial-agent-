from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.decision import AffordabilityStatus, CandidateKind, RecommendedMethod, RejectionReason
from buyorwait.domain import Direction, EventStatus, EventType, Flexibility, PaymentMethod
from buyorwait.evidence import FactType
from buyorwait.financial_state import ExclusionReason
from buyorwait.forecast import build_financial_state
from buyorwait.forecast_config import ForecastConfig
from buyorwait.output_record import assemble_output_record
from buyorwait.planning import (
    DecisionConfig,
    apply_spending_changes,
    build_spending_change_options,
    simulate_plan,
)
from tests.test_decision import FULL_TODAY, option, scenario
from tests.test_forecast import REQUEST_DATE, bundle, evidence_bundle, event, fact, monthly_series

DAY = timedelta(days=1)
HORIZON_END = REQUEST_DATE + timedelta(days=90)


def scheduled(event_id, when, amount, **kwargs):
    kwargs.setdefault("category", "rent")
    return event(event_id, when, amount, status=EventStatus.SCHEDULED, **kwargs)


def income(event_id, when, amount, **kwargs):
    return event(
        event_id,
        when,
        amount,
        event_type=EventType.INCOME,
        direction=Direction.CREDIT,
        status=kwargs.pop("status", EventStatus.SCHEDULED),
        category=kwargs.pop("category", "salary"),
        **kwargs,
    )


# ---------------------------------------------------------------- A. boundaries


def test_request_date_falling_on_an_income_date_counts_that_income():
    events = (scheduled("bill", REQUEST_DATE, "9500"), income("pay", REQUEST_DATE, "5000"))
    forecast = build_financial_state(bundle(events))
    assert forecast.daily_states[0].closing_balance == Decimal("5500")
    assert not forecast.breaches_minimum


def test_request_date_falling_on_an_expense_date_counts_that_expense():
    forecast = build_financial_state(bundle((scheduled("bill", REQUEST_DATE, "500"),)))
    assert forecast.daily_states[0].closing_balance == Decimal("9500")


def test_event_one_day_before_request_date_is_history_not_forecast():
    events = (scheduled("bill", REQUEST_DATE - DAY, "500"),)
    forecast = build_financial_state(bundle(events))
    assert not forecast.events
    assert forecast.starting_state.reserved_total == Decimal("500")


def test_event_one_day_after_request_date_is_forecast():
    forecast = build_financial_state(bundle((scheduled("bill", REQUEST_DATE + DAY, "500"),)))
    assert [e.when for e in forecast.events] == [REQUEST_DATE + DAY]


def test_event_exactly_on_day_ninety_is_inside_the_window():
    forecast = build_financial_state(bundle((scheduled("bill", HORIZON_END, "500"),)))
    assert [e.when for e in forecast.events] == [HORIZON_END]
    assert forecast.daily_states[-1].closing_balance == Decimal("9500")


def test_event_on_day_ninety_one_is_outside_the_window():
    forecast = build_financial_state(bundle((scheduled("bill", HORIZON_END + DAY, "500"),)))
    assert not forecast.events
    assert any(i.reason is ExclusionReason.OUTSIDE_WINDOW for i in forecast.excluded)


def test_recurrence_exactly_at_one_cadence_is_projected():
    series = tuple(
        event(f"m{i}", date(2025, 5 + i, 1), "100", category="gym") for i in range(3)
    )
    forecast = build_financial_state(bundle(series))
    assert [e.when for e in forecast.events][:1] == [date(2025, 8, 1)]


def test_recurrence_slightly_beyond_one_cadence_is_dropped_as_lapsed():
    series = tuple(
        event(f"m{i}", date(2025, 4 + i, 1), "100", category="gym") for i in range(3)
    )
    forecast = build_financial_state(bundle(series))
    assert not forecast.events


def test_desired_completion_on_day_zero_only_admits_a_same_day_plan():
    _b, _f, _s, rec = scenario(options=(FULL_TODAY,), due_days=0)
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_NOW
    assert rec.payment_plan == ((REQUEST_DATE, Decimal("5000")),)


def test_desired_completion_on_day_ninety_admits_a_late_wait_plan():
    events = (scheduled("bill", REQUEST_DATE, "8500"), income("pay", REQUEST_DATE + timedelta(days=89), "9000"))
    _b, _f, _s, rec = scenario(events=events, options=(FULL_TODAY,), requested="5000", due_days=90)
    assert rec.earliest_date_for_full_payment == REQUEST_DATE + timedelta(days=89)
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_LATER


def test_income_after_the_horizon_cannot_rescue_a_request():
    events = (scheduled("bill", REQUEST_DATE, "8500"), income("pay", HORIZON_END + DAY, "9000"))
    _b, _f, _s, rec = scenario(events=events, options=(FULL_TODAY,), requested="5000", due_days=90)
    assert rec.earliest_date_for_full_payment is None
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


# ---------------------------------------------------------------- B. balance safety


def test_balance_exactly_at_the_minimum_leaves_no_safe_amount():
    _b, _f, _s, rec = scenario(balance="1000", minimum="1000", requested="500", options=(FULL_TODAY,))
    assert rec.amount_safe_to_pay == Decimal("0")
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


def test_balance_one_cent_above_the_minimum_yields_one_cent():
    _b, _f, _s, rec = scenario(balance="1000.01", minimum="1000", requested="500", options=(FULL_TODAY,))
    assert rec.amount_safe_to_pay == Decimal("0.01")


def test_balance_below_the_minimum_never_produces_a_plan():
    _b, _f, _s, rec = scenario(balance="999", minimum="1000", requested="500", options=(FULL_TODAY,))
    assert rec.amount_safe_to_pay == Decimal("0")
    assert rec.earliest_date_for_full_payment is None
    assert rec.recommended_payment_method is RecommendedMethod.NOT_RECOMMENDED


def test_payment_exactly_at_the_safe_amount_is_safe_and_one_cent_more_is_not():
    events = (scheduled("bill", REQUEST_DATE + timedelta(days=10), "3000"),)
    _b, forecast, spec, rec = scenario(events=events, requested="100000", options=(FULL_TODAY,))
    amount = rec.amount_safe_to_pay
    assert not simulate_plan(forecast, spec, ((REQUEST_DATE, amount),)).breaches_minimum
    assert simulate_plan(forecast, spec, ((REQUEST_DATE, amount + Decimal("0.01")),)).breaches_minimum


def test_temporary_deficit_that_recovers_still_blocks_the_forecast():
    events = (
        scheduled("bill", REQUEST_DATE + timedelta(days=5), "9500"),
        income("pay", REQUEST_DATE + timedelta(days=20), "20000"),
    )
    _b, forecast, _s, rec = scenario(events=events, requested="1000", options=(FULL_TODAY,))
    assert forecast.breaches_minimum
    assert rec.amount_safe_to_pay == Decimal("0")
    assert rec.earliest_date_for_full_payment is None


# ---------------------------------------------------------------- C. lifecycle


def test_linked_record_ordering_does_not_change_the_forecast():
    forward = (
        event("a", REQUEST_DATE - timedelta(days=10), "300", status=EventStatus.PENDING, category="shopping", linked="b"),
        event("b", REQUEST_DATE - timedelta(days=9), "300", category="shopping"),
    )
    reversed_order = tuple(reversed(forward))
    first = build_financial_state(bundle(forward))
    second = build_financial_state(bundle(reversed_order))
    assert first.starting_state.reserved_total == second.starting_state.reserved_total == Decimal("0")


def test_duplicating_a_superseded_record_creates_no_extra_cash_flow():
    base = (
        event("a", REQUEST_DATE - timedelta(days=10), "300", status=EventStatus.PENDING, category="shopping", linked="b"),
        event("b", REQUEST_DATE - timedelta(days=9), "300", category="shopping"),
    )
    extra = base + (
        event("c", REQUEST_DATE - timedelta(days=8), "300", status=EventStatus.PENDING, category="shopping", linked="b"),
    )
    assert (
        build_financial_state(bundle(base)).starting_state.available_cash
        == build_financial_state(bundle(extra)).starting_state.available_cash
    )


def test_failed_and_cancelled_records_never_reserve_cash():
    events = (
        event("f", REQUEST_DATE - timedelta(days=5), "400", status=EventStatus.FAILED),
        event("c", REQUEST_DATE - timedelta(days=4), "400", status=EventStatus.CANCELLED),
    )
    forecast = build_financial_state(bundle(events))
    assert forecast.starting_state.available_cash == Decimal("10000")


def test_realized_sale_and_unrealized_valuation_are_treated_differently():
    shared = event(
        "buy", REQUEST_DATE - timedelta(days=40), "1000", event_type=EventType.INVESTMENT_PURCHASE, category="investment"
    )
    unrealized = (
        shared,
        event(
            "val",
            REQUEST_DATE - timedelta(days=5),
            "5000",
            event_type=EventType.INVESTMENT_VALUATION,
            direction=Direction.NON_CASH,
            status=EventStatus.UNREALIZED,
            category="investment",
            linked="buy",
        ),
    )
    realized = (
        shared,
        event(
            "sale",
            REQUEST_DATE - timedelta(days=5),
            "5000",
            event_type=EventType.INVESTMENT_SALE,
            direction=Direction.CREDIT,
            category="investment",
            linked="buy",
        ),
    )
    a = build_financial_state(bundle(unrealized))
    b = build_financial_state(bundle(realized))
    assert a.starting_state.unrealized_investment_value == Decimal("5000")
    assert b.starting_state.unrealized_investment_value == Decimal("0")
    assert a.starting_state.available_cash == b.starting_state.available_cash


# ---------------------------------------------------------------- D. recurrence


def test_a_single_occurrence_is_never_projected():
    forecast = build_financial_state(bundle((event("only", REQUEST_DATE - timedelta(days=5), "400", category="medical"),)))
    assert not forecast.events


def test_two_occurrences_project_under_the_default_threshold():
    events = monthly_series("g", "gym", ["100", "100"])
    forecast = build_financial_state(bundle(events))
    assert forecast.events


def test_lapsed_income_is_still_projected_by_default_but_configurable():
    lapsed = tuple(income(f"i{i}", date(2025, 2 + i, 15), "900", status=EventStatus.SETTLED) for i in range(3))
    default = build_financial_state(bundle(lapsed))
    strict = build_financial_state(bundle(lapsed), config=ForecastConfig(staleness_scope="all"))
    assert [e for e in default.events if e.category == "salary"]
    assert not [e for e in strict.events if e.category == "salary"]


def test_recurrence_with_a_missing_amount_never_invents_one():
    events = monthly_series("g", "gym", ["100", "100"]) + (
        event("blank", REQUEST_DATE - timedelta(days=3), None, status=EventStatus.PENDING, category="gym"),
    )
    forecast = build_financial_state(bundle(events))
    assert all(e.original_amount is not None for e in forecast.events)
    assert [o for o in forecast.unresolved_obligations if o.reference_id == "blank"]


# ---------------------------------------------------------------- E. payment-method gates


@pytest.mark.parametrize(
    "accepted,expected_method",
    [
        ((PaymentMethod.FULL_PAYMENT,), RecommendedMethod.FULL_PAYMENT),
        ((PaymentMethod.INSTALLMENTS,), RecommendedMethod.INSTALLMENTS),
        ((PaymentMethod.PARTIAL_PAYMENT,), RecommendedMethod.NOT_RECOMMENDED),
    ],
)
def test_user_willingness_alone_decides_which_safe_method_is_recommended(accepted, expected_method):
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    _b, _f, _s, rec = scenario(
        options=(FULL_TODAY, installments), accepted=accepted, max_installments=6, allows_partial=False
    )
    assert rec.recommended_payment_method is expected_method


def test_capacity_eligibility_and_preference_are_recorded_independently():
    long_option = option("payment_option_02", PaymentMethod.INSTALLMENTS, "500", 12, REQUEST_DATE, 30, "1000", "6000")
    _b, _f, _s, rec = scenario(
        options=(FULL_TODAY, long_option), accepted=(PaymentMethod.FULL_PAYMENT,), max_installments=6
    )
    installment = next(e for e in rec.candidates if e.candidate.kind is CandidateKind.INSTALLMENTS)
    assert installment.gates.financial_capacity
    assert not installment.gates.method_eligibility
    assert not installment.gates.user_preference
    full = next(e for e in rec.candidates if e.candidate.kind is CandidateKind.FULL_PAYMENT_TODAY)
    assert full.gates.passes


def test_earliest_date_is_capacity_derived_even_when_full_payment_is_rejected():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    _b, _f, _s, rec = scenario(
        options=(FULL_TODAY, installments), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    assert rec.earliest_date_for_full_payment == REQUEST_DATE
    assert rec.recommended_payment_method is RecommendedMethod.INSTALLMENTS


def test_a_safe_but_ineligible_method_is_never_recommended():
    long_option = option("payment_option_02", PaymentMethod.INSTALLMENTS, "500", 12, REQUEST_DATE, 30, "1000", "6000")
    _b, _f, _s, rec = scenario(
        options=(long_option,), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    assert rec.recommended_payment_method is RecommendedMethod.NOT_RECOMMENDED
    assert RejectionReason.INSTALLMENT_TERM_TOO_LONG in rec.blocking_reasons


# ---------------------------------------------------------------- F. partial payment


def test_partial_payment_is_not_offered_when_the_request_forbids_it():
    events = (scheduled("cost", REQUEST_DATE + DAY, "6500"), income("pay", REQUEST_DATE + timedelta(days=20), "9000"))
    _b, _f, _s, rec = scenario(
        events=events,
        options=(FULL_TODAY,),
        accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.PARTIAL_PAYMENT),
        allows_partial=False,
    )
    assert not [e for e in rec.candidates if e.candidate.kind is CandidateKind.PARTIAL_PAYMENT]


def test_partial_payment_is_not_offered_when_the_user_rejects_it():
    events = (scheduled("cost", REQUEST_DATE + DAY, "6500"), income("pay", REQUEST_DATE + timedelta(days=20), "9000"))
    _b, _f, _s, rec = scenario(
        events=events, options=(FULL_TODAY,), accepted=(PaymentMethod.FULL_PAYMENT,), allows_partial=True
    )
    assert not [e for e in rec.candidates if e.candidate.kind is CandidateKind.PARTIAL_PAYMENT]


def test_partial_payment_is_not_offered_when_the_safe_amount_is_zero_or_full():
    _b, _f, _s, full_safe = scenario(
        options=(FULL_TODAY,),
        accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.PARTIAL_PAYMENT),
        allows_partial=True,
    )
    assert not [e for e in full_safe.candidates if e.candidate.kind is CandidateKind.PARTIAL_PAYMENT]

    _b2, _f2, _s2, nothing_safe = scenario(
        balance="1000",
        minimum="1000",
        options=(FULL_TODAY,),
        accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.PARTIAL_PAYMENT),
        allows_partial=True,
    )
    assert not [e for e in nothing_safe.candidates if e.candidate.kind is CandidateKind.PARTIAL_PAYMENT]


def test_partial_payment_completing_after_the_deadline_is_rejected():
    events = (scheduled("cost", REQUEST_DATE + DAY, "6500"), income("pay", REQUEST_DATE + timedelta(days=50), "9000"))
    _b, _f, _s, rec = scenario(
        events=events,
        options=(FULL_TODAY,),
        accepted=(PaymentMethod.PARTIAL_PAYMENT,),
        allows_partial=True,
        due_days=20,
    )
    partial = [e for e in rec.candidates if e.candidate.kind is CandidateKind.PARTIAL_PAYMENT]
    assert partial
    assert RejectionReason.COMPLETION_AFTER_DEADLINE in partial[0].rejection_reasons
    assert rec.recommended_payment_method is not RecommendedMethod.PARTIAL_PAYMENT


# ---------------------------------------------------------------- G. installments


def test_installment_options_are_tie_broken_by_payment_option_id():
    first = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    second = option("payment_option_03", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    _b, _f, _s, rec = scenario(
        options=(second, first), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    assert rec.selected_candidate_id == "installments:payment_option_02"


def test_selected_installment_plan_matches_the_supplied_option_exactly():
    supplied = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE + DAY, 12, "100", "5100")
    _b, _f, _s, rec = scenario(
        options=(supplied,), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    expected = tuple(
        (supplied.first_payment_date + timedelta(days=12 * i), supplied.payment_amount) for i in range(5)
    )
    assert rec.payment_plan == expected


def test_zero_fee_and_fee_bearing_options_are_ranked_by_total_cost():
    zero_fee = option("payment_option_03", PaymentMethod.INSTALLMENTS, "1000", 5, REQUEST_DATE, 10, "0", "5000")
    fee = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1100", 5, REQUEST_DATE, 10, "500", "5500")
    _b, _f, _s, rec = scenario(
        options=(fee, zero_fee), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    assert rec.selected_candidate_id == "installments:payment_option_03"


def test_an_installment_option_completing_after_the_deadline_is_rejected():
    late = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1000", 5, REQUEST_DATE, 30, "0", "5000")
    _b, _f, _s, rec = scenario(
        options=(late,), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6, due_days=60
    )
    evaluation = next(e for e in rec.candidates if e.candidate.kind is CandidateKind.INSTALLMENTS)
    assert RejectionReason.COMPLETION_AFTER_DEADLINE in evaluation.rejection_reasons


def test_installment_payments_beyond_the_horizon_are_never_silently_ignored():
    beyond = option("payment_option_02", PaymentMethod.INSTALLMENTS, "4000", 4, REQUEST_DATE, 40, "0", "16000")
    _b, _f, _s, rec = scenario(
        options=(beyond,), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6, due_days=90, requested="16000"
    )
    evaluation = next(e for e in rec.candidates if e.candidate.kind is CandidateKind.INSTALLMENTS)
    assert evaluation.payments_outside_window > 0
    assert RejectionReason.COMPLETION_AFTER_DEADLINE in evaluation.rejection_reasons


# ---------------------------------------------------------------- H. spending changes


def test_reduce_to_simulates_the_amount_that_is_actually_published():
    varying = monthly_series(
        "s", "streaming", ["1000", "600", "500"], flexibility=Flexibility.REDUCIBLE, minimum_allowed="300"
    )
    request_bundle, forecast, spec, rec = scenario(
        events=varying,
        balance="8000",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
        reduce=("streaming",),
        stop=(),
    )
    options = build_spending_change_options(request_bundle, forecast, DecisionConfig())
    assert options
    change = options[0]
    adjusted = apply_spending_changes(forecast.events, (change,))
    reduced = [e for e in adjusted if e.category == "streaming"]
    assert reduced
    for occurrence in reduced:
        assert occurrence.original_amount == change.new_amount


def test_a_protected_category_is_never_offered_as_a_change():
    groceries = monthly_series(
        "g", "groceries", ["900", "900", "900"], flexibility=Flexibility.REDUCIBLE, minimum_allowed="100"
    )
    request_bundle, forecast, _s, rec = scenario(
        events=groceries,
        balance="6500",
        requested="5000",
        options=(FULL_TODAY,),
        reduce=("groceries",),
        protect=("groceries",),
    )
    assert not build_spending_change_options(request_bundle, forecast, DecisionConfig())
    assert not rec.spending_changes


def test_a_fixed_recurring_expense_is_never_offered_as_a_change():
    fixed = monthly_series("u", "utilities", ["900", "900", "900"], flexibility=Flexibility.FIXED)
    request_bundle, forecast, _s, _rec = scenario(
        events=fixed, balance="6500", requested="5000", options=(FULL_TODAY,), reduce=("utilities",), stop=("utilities",)
    )
    assert not build_spending_change_options(request_bundle, forecast, DecisionConfig())


def test_a_reduction_is_never_offered_when_the_floor_equals_the_current_amount():
    at_floor = monthly_series(
        "s", "streaming", ["300", "300", "300"], flexibility=Flexibility.REDUCIBLE, minimum_allowed="300"
    )
    request_bundle, forecast, _s, _rec = scenario(
        events=at_floor, balance="6500", requested="5000", options=(FULL_TODAY,), reduce=("streaming",), stop=()
    )
    assert not build_spending_change_options(request_bundle, forecast, DecisionConfig())


def test_no_change_is_recommended_when_the_plan_is_already_safe():
    streaming = monthly_series("s", "streaming", ["900", "900", "900"], flexibility=Flexibility.STOPPABLE)
    _b, _f, _s, rec = scenario(events=streaming, balance="20000", requested="5000", options=(FULL_TODAY,))
    assert not rec.spending_changes
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_NOW


def test_the_engine_never_recommends_more_than_three_changes():
    events = ()
    categories = ["streaming", "gym", "dining_out", "hobby", "music"]
    for index, category in enumerate(categories):
        events = events + monthly_series(f"c{index}", category, ["400", "400", "400"], flexibility=Flexibility.STOPPABLE)
    _b, _f, _s, rec = scenario(
        events=events,
        balance="7000",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
        reduce=tuple(categories),
        stop=tuple(categories),
    )
    assert len(rec.spending_changes) <= 3
    assert len({c.event_id for c in rec.spending_changes}) == len(rec.spending_changes)


# ---------------------------------------------------------------- I. evidence


def test_a_cancellation_fact_removes_a_scheduled_obligation():
    events = (scheduled("bill", REQUEST_DATE + timedelta(days=5), "6000"),)
    without = scenario(events=events, requested="5000", options=(FULL_TODAY,))[3]
    evidence = evidence_bundle(fact(FactType.EVENT_CANCELLATION, effective=REQUEST_DATE, event_id="bill"))
    with_cancel = scenario(events=events, requested="5000", options=(FULL_TODAY,), evidence=evidence)[3]
    assert without.affordability_status is not AffordabilityStatus.AFFORDABLE_NOW
    assert with_cancel.affordability_status is AffordabilityStatus.AFFORDABLE_NOW


def test_an_untrusted_message_cannot_create_income():
    evidence = evidence_bundle(
        fact(FactType.SALARY_CONFIRMED, amount="90000", effective=REQUEST_DATE + DAY, trusted=False)
    )
    _b, forecast, _s, rec = scenario(
        balance="1200", minimum="1000", requested="5000", options=(FULL_TODAY,), evidence=evidence
    )
    assert forecast.cumulative_future_income == Decimal("0")
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


def test_an_unresolved_fact_never_moves_the_balance_in_either_direction():
    evidence = evidence_bundle(
        fact(FactType.EXPENSE_NEW_RECURRING, amount=None, effective=REQUEST_DATE + DAY, category="childcare")
    )
    baseline = scenario(options=(FULL_TODAY,))[1]
    flagged = scenario(options=(FULL_TODAY,), evidence=evidence)[1]
    assert baseline.minimum_projected_balance == flagged.minimum_projected_balance
    assert flagged.unresolved_obligations


# ---------------------------------------------------------------- J. output contract


def test_output_record_never_publishes_a_change_absent_from_the_decision():
    streaming = monthly_series("s", "streaming", ["900", "900", "900"], flexibility=Flexibility.STOPPABLE)
    _b, _f, spec, rec = scenario(events=streaming, balance="6500", requested="5000", options=(FULL_TODAY,))
    record = assemble_output_record(rec, spec)
    published = [] if record.spending_changes_needed == "none" else record.spending_changes_needed.split("|")
    assert len(published) == len(rec.spending_changes)


def test_output_money_never_exceeds_two_decimals():
    events = (scheduled("bill", REQUEST_DATE + timedelta(days=10), "3333.335"),)
    _b, _f, spec, rec = scenario(events=events, requested="100000", options=(FULL_TODAY,))
    record = assemble_output_record(rec, spec)
    assert Decimal(record.amount_safe_to_pay).as_tuple().exponent >= -2


def test_missing_exchange_rate_produces_a_conservative_record():
    request_bundle, forecast, _s, _rec = scenario(options=(FULL_TODAY,))
    from buyorwait.planning import build_recommendation, normalize_request_in_currency

    spec = normalize_request_in_currency(request_bundle, forecast, "USD")
    rec = build_recommendation(request_bundle, forecast, DecisionConfig(), spec)
    record = assemble_output_record(rec, spec)
    assert record.amount_safe_to_pay == "0"
    assert record.affordability_status == "not_affordable"
    assert record.payment_plan == "none"
    assert record.decision_explanation


def test_an_installment_option_that_underfunds_the_request_is_rejected():
    short = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1000", 4, REQUEST_DATE, 10, "0", "4000")
    _b, _f, _s, rec = scenario(
        options=(short,), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6, requested="5000"
    )
    evaluation = next(e for e in rec.candidates if e.candidate.kind is CandidateKind.INSTALLMENTS)
    assert RejectionReason.INSTALLMENT_TOTAL_BELOW_REQUEST in evaluation.rejection_reasons
    assert rec.recommended_payment_method is not RecommendedMethod.INSTALLMENTS


def test_completing_by_the_deadline_outranks_every_other_criterion():
    from buyorwait.decision import CandidatePlan
    from buyorwait.planning import selection_key

    on_time = CandidatePlan(
        candidate_id="on_time",
        kind=CandidateKind.INSTALLMENTS,
        method=RecommendedMethod.INSTALLMENTS,
        payment_option_id="payment_option_09",
        payments=((REQUEST_DATE + timedelta(days=5), Decimal("5000")),),
        total_paid=Decimal("9999"),
        financing_fee=Decimal("4999"),
        spending_changes=(),
        completes_request=True,
        completion_date=REQUEST_DATE + timedelta(days=5),
        provenance="test",
    )
    late = CandidatePlan(
        candidate_id="late",
        kind=CandidateKind.FULL_PAYMENT_LATER,
        method=RecommendedMethod.WAIT,
        payment_option_id="payment_option_01",
        payments=((REQUEST_DATE, Decimal("5000")),),
        total_paid=Decimal("5000"),
        financing_fee=Decimal("0"),
        spending_changes=(),
        completes_request=False,
        completion_date=REQUEST_DATE + timedelta(days=95),
        provenance="test",
    )

    class Stub:
        def __init__(self, candidate):
            self.candidate = candidate

    assert selection_key(Stub(on_time)) < selection_key(Stub(late))


def test_an_earlier_starting_plan_wins_when_cost_is_identical():
    events = (
        income("first", REQUEST_DATE + timedelta(days=10), "900", category="salary"),
        income("second", REQUEST_DATE + timedelta(days=30), "900", category="contract_fee"),
        income("late", REQUEST_DATE + timedelta(days=70), "9000", category="annual_bonus"),
    )
    zero_fee = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1000", 3, REQUEST_DATE, 20, "0", "3000")
    _b, _f, _s, rec = scenario(
        events=events,
        options=(FULL_TODAY, zero_fee),
        balance="2500",
        minimum="1000",
        requested="3000",
        accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.INSTALLMENTS),
        max_installments=6,
        due_days=45,
    )
    assert rec.recommended_payment_method is RecommendedMethod.INSTALLMENTS
    assert rec.payment_plan[0][0] == REQUEST_DATE


def test_a_late_wait_is_still_offered_when_nothing_completes_on_time():
    events = (
        income("first", REQUEST_DATE + timedelta(days=10), "1100", category="salary"),
        income("late", REQUEST_DATE + timedelta(days=70), "9000", category="annual_bonus"),
    )
    _b, _f, _s, rec = scenario(
        events=events, options=(FULL_TODAY,), balance="2500", minimum="1000", requested="3000", due_days=40
    )
    assert rec.recommended_payment_method is RecommendedMethod.WAIT
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_LATER
    assert rec.earliest_date_for_full_payment > REQUEST_DATE + timedelta(days=40)
