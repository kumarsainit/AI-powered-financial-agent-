from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.bundle import build_request_bundle
from buyorwait.decision import AffordabilityStatus, RecommendedMethod
from buyorwait.decision_invariants import check_recommendation_invariants
from buyorwait.domain import Direction, EventStatus, EventType, Flexibility, PaymentMethod
from buyorwait.evidence import FactType
from buyorwait.evidence_pipeline import build_evidence_bundle
from buyorwait.forecast import build_financial_state
from buyorwait.ingestion import load_dataset, load_sample_request_user_ids
from buyorwait.planning import build_recommendation, normalize_request, simulate_plan
from buyorwait.usage import UsageTracker
from tests.paths import DATASET_DIR
from tests.test_decision import FULL_TODAY, option, scenario
from tests.test_forecast import REQUEST_DATE, evidence_bundle, event, fact, monthly_series

PRODUCTION_SAMPLE = ("request_26", "request_31", "request_60", "request_119", "request_200")


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATASET_DIR)


@pytest.fixture(scope="module")
def tracker():
    return UsageTracker()


def recommend(dataset, tracker, request_id):
    bundle = build_request_bundle(dataset, request_id)
    forecast = build_financial_state(bundle, build_evidence_bundle(bundle, tracker))
    spec = normalize_request(bundle, forecast)
    return bundle, forecast, spec, build_recommendation(bundle, forecast, spec=spec)


@pytest.mark.parametrize("request_id", PRODUCTION_SAMPLE)
def test_production_requests_produce_valid_recommendations(dataset, tracker, request_id):
    _bundle, forecast, spec, recommendation = recommend(dataset, tracker, request_id)
    check_recommendation_invariants(recommendation, forecast, spec)
    assert Decimal("0") <= recommendation.amount_safe_to_pay <= spec.requested_amount_home


@pytest.mark.parametrize("request_id", PRODUCTION_SAMPLE)
def test_production_recommendations_are_deterministic(dataset, tracker, request_id):
    first = recommend(dataset, tracker, request_id)[3]
    second = recommend(dataset, tracker, request_id)[3]
    assert first.selected_candidate_id == second.selected_candidate_id
    assert first.amount_safe_to_pay == second.amount_safe_to_pay
    assert first.payment_plan == second.payment_plan
    assert first.affordability_status == second.affordability_status
    assert [c.candidate.candidate_id for c in first.candidates] == [
        c.candidate.candidate_id for c in second.candidates
    ]


def test_sample_users_never_enter_production_decisions(dataset, tracker):
    sample_users = load_sample_request_user_ids(DATASET_DIR)
    for request_id in PRODUCTION_SAMPLE:
        bundle, _forecast, _spec, recommendation = recommend(dataset, tracker, request_id)
        assert bundle.profile.user_id not in sample_users
        assert recommendation.user_id not in sample_users


@pytest.mark.parametrize("request_id", PRODUCTION_SAMPLE)
def test_selected_plan_is_re_validated_by_the_simulator(dataset, tracker, request_id):
    _bundle, forecast, spec, recommendation = recommend(dataset, tracker, request_id)
    if not recommendation.payment_plan:
        pytest.skip("no plan recommended")
    outcome = simulate_plan(forecast, spec, recommendation.payment_plan, recommendation.spending_changes)
    assert not outcome.breaches_minimum


def test_purchase_fits_today_but_creates_a_future_deficit():
    events = (
        event("rent", REQUEST_DATE + timedelta(days=40), "5500", status=EventStatus.SCHEDULED, category="rent"),
    )
    _b, _f, _s, rec = scenario(events=events, balance="10000", minimum="1000", requested="5000", options=(FULL_TODAY,))
    assert rec.affordability_status is not AffordabilityStatus.AFFORDABLE_NOW
    assert rec.amount_safe_to_pay == Decimal("3500")


def test_salary_arrives_one_day_after_an_installment():
    installments = option(
        "payment_option_02", PaymentMethod.INSTALLMENTS, "4500", 2, REQUEST_DATE + timedelta(days=9), 10, "1000", "9000"
    )
    events = (
        event("bill", REQUEST_DATE, "5000", status=EventStatus.SCHEDULED, category="rent"),
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
    _b, _f, _s, rec = scenario(
        events=events,
        options=(installments,),
        accepted=(PaymentMethod.INSTALLMENTS,),
        max_installments=6,
        requested="8000",
    )
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE


def test_lower_instalment_but_higher_total_cost_loses():
    low_monthly = option(
        "payment_option_02", PaymentMethod.INSTALLMENTS, "900", 6, REQUEST_DATE, 10, "400", "5400"
    )
    higher_monthly = option(
        "payment_option_03", PaymentMethod.INSTALLMENTS, "1040", 5, REQUEST_DATE, 10, "200", "5200"
    )
    _b, _f, _s, rec = scenario(
        options=(low_monthly, higher_monthly),
        accepted=(PaymentMethod.INSTALLMENTS,),
        max_installments=6,
    )
    assert rec.selected_candidate_id == "installments:payment_option_03"


def test_flexible_reduction_that_is_insufficient_does_not_rescue_a_plan():
    streaming = monthly_series("s", "streaming", ["100", "100", "100"], flexibility=Flexibility.STOPPABLE)
    _b, _f, _s, rec = scenario(
        events=streaming, balance="5200", minimum="1000", requested="5000", options=(FULL_TODAY,)
    )
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert not rec.spending_changes


def test_reduction_below_the_allowed_minimum_is_never_used_to_make_a_plan_look_safe():
    streaming = monthly_series(
        "s", "streaming", ["900", "900", "900"], flexibility=Flexibility.REDUCIBLE, minimum_allowed="850"
    )
    _b, _f, _s, rec = scenario(
        events=streaming,
        balance="5300",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
        reduce=("streaming",),
        stop=(),
    )
    assert rec.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert not rec.spending_changes


def test_a_cancellation_message_frees_capacity_for_the_purchase():
    gym = monthly_series("g", "gym", ["1200", "1200", "1200"])
    without_message = scenario(
        events=gym, balance="8000", minimum="1000", requested="5000", options=(FULL_TODAY,)
    )[3]
    evidence = evidence_bundle(
        fact(FactType.EXPENSE_TERMINATED, effective=REQUEST_DATE, category="gym")
    )
    with_message = scenario(
        events=gym,
        balance="8000",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
        evidence=evidence,
    )[3]
    assert without_message.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert with_message.affordability_status is AffordabilityStatus.AFFORDABLE_NOW


def test_a_new_recurring_expense_starting_after_the_purchase_blocks_it():
    evidence = evidence_bundle(
        fact(
            FactType.EXPENSE_NEW_RECURRING,
            amount="2500",
            effective=REQUEST_DATE + timedelta(days=5),
            category="childcare",
            recurrence="monthly",
        )
    )
    _b, _f, _s, rec = scenario(
        balance="10000", minimum="1000", requested="5000", options=(FULL_TODAY,), evidence=evidence
    )
    assert rec.affordability_status is not AffordabilityStatus.AFFORDABLE_NOW


def test_uncertain_income_does_not_rescue_an_otherwise_unsafe_plan():
    weak_dates = [REQUEST_DATE - timedelta(days=d) for d in (120, 117, 40, 5)]
    weak_income = tuple(
        event(
            f"wi{i}",
            when,
            "4000",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            category="salary",
        )
        for i, when in enumerate(sorted(weak_dates))
    )
    _b, forecast, _s, rec = scenario(
        events=weak_income, balance="5200", minimum="1000", requested="5000", options=(FULL_TODAY,)
    )
    projected = [e for e in forecast.events if e.category == "salary"]
    assert projected
    assert rec.affordability_status is not AffordabilityStatus.AFFORDABLE_NOW
