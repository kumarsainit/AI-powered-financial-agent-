from __future__ import annotations

import random
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait import ingestion
from buyorwait.decision import RecommendedMethod
from buyorwait.domain import EventStatus, Flexibility, PaymentMethod
from buyorwait.pipeline import run_pipeline
from tests.paths import DATASET_DIR
from tests.test_decision import FULL_TODAY, option, scenario
from tests.test_forecast import REQUEST_DATE, evidence_bundle, event, fact, monthly_series

DAY = timedelta(days=1)
BASE_EVENTS = monthly_series("g", "groceries", ["300", "320", "310"]) + monthly_series(
    "s", "streaming", ["120", "120", "120"], flexibility=Flexibility.REDUCIBLE_OR_STOPPABLE, minimum_allowed="60"
)


def recommend(**kwargs):
    kwargs.setdefault("events", BASE_EVENTS)
    kwargs.setdefault("options", (FULL_TODAY,))
    return scenario(**kwargs)[3]


def comparable(recommendation):
    return (
        recommendation.amount_safe_to_pay,
        recommendation.affordability_status,
        recommendation.recommended_payment_method,
        recommendation.payment_plan,
        recommendation.earliest_date_for_full_payment,
        tuple((c.action, c.event_id, c.new_amount) for c in recommendation.spending_changes),
    )


def test_an_unrelated_one_off_event_does_not_change_the_recommendation():
    baseline = recommend()
    with_noise = recommend(
        events=BASE_EVENTS + (event("noise", REQUEST_DATE - timedelta(days=200), "40", category="stationery"),)
    )
    assert comparable(baseline) == comparable(with_noise)


def test_reordering_input_events_does_not_change_the_recommendation():
    baseline = recommend()
    shuffled = list(BASE_EVENTS)
    random.Random(11).shuffle(shuffled)
    assert comparable(baseline) == comparable(recommend(events=tuple(shuffled)))


def test_reordering_linked_lifecycle_records_does_not_change_the_recommendation():
    linked = BASE_EVENTS + (
        event("p", REQUEST_DATE - timedelta(days=6), "90", status=EventStatus.PENDING, category="shopping", linked="q"),
        event("q", REQUEST_DATE - timedelta(days=5), "90", category="shopping"),
    )
    assert comparable(recommend(events=linked)) == comparable(recommend(events=tuple(reversed(linked))))


def test_duplicating_a_superseded_record_does_not_change_the_recommendation():
    linked = BASE_EVENTS + (
        event("p", REQUEST_DATE - timedelta(days=6), "90", status=EventStatus.PENDING, category="shopping", linked="q"),
        event("q", REQUEST_DATE - timedelta(days=5), "90", category="shopping"),
    )
    duplicated = linked + (
        event("p2", REQUEST_DATE - timedelta(days=6), "90", status=EventStatus.PENDING, category="shopping", linked="q"),
    )
    assert comparable(recommend(events=linked)) == comparable(recommend(events=duplicated))


def test_an_unrelated_message_does_not_change_the_recommendation():
    from buyorwait.evidence import FactType

    baseline = recommend()
    noisy = recommend(evidence=evidence_bundle(fact(FactType.IRRELEVANT, effective=REQUEST_DATE)))
    assert comparable(baseline) == comparable(noisy)


def test_a_lapsed_expense_series_does_not_resurrect_spending():
    from datetime import date

    lapsed = tuple(event(f"old{i}", date(2024, 3 + i, 5), "5000", category="tuition") for i in range(3))
    assert comparable(recommend()) == comparable(recommend(events=BASE_EVENTS + lapsed))


@pytest.mark.parametrize("extra", ["1", "500", "5000"])
def test_more_available_cash_never_reduces_the_safe_amount(extra):
    baseline = recommend(balance="9000", requested="100000")
    richer = recommend(balance=str(9000 + int(extra)), requested="100000")
    assert richer.amount_safe_to_pay >= baseline.amount_safe_to_pay


@pytest.mark.parametrize("minimum", ["1000", "2000", "4000"])
def test_a_higher_minimum_balance_never_increases_the_safe_amount(minimum):
    lower = recommend(balance="9000", minimum="500", requested="100000")
    higher = recommend(balance="9000", minimum=minimum, requested="100000")
    assert higher.amount_safe_to_pay <= lower.amount_safe_to_pay


def test_a_larger_request_never_lowers_the_safe_amount():
    small = recommend(balance="9000", requested="100")
    large = recommend(balance="9000", requested="100000")
    assert large.amount_safe_to_pay >= small.amount_safe_to_pay


def test_removing_a_payment_option_never_produces_that_method():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    with_option = recommend(
        options=(FULL_TODAY, installments), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    without_option = recommend(options=(FULL_TODAY,), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6)
    assert with_option.recommended_payment_method is RecommendedMethod.INSTALLMENTS
    assert without_option.recommended_payment_method is not RecommendedMethod.INSTALLMENTS


def test_removing_user_willingness_never_produces_that_method():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    willing = recommend(
        options=(FULL_TODAY, installments), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    unwilling = recommend(
        options=(FULL_TODAY, installments), accepted=(PaymentMethod.FULL_PAYMENT,), max_installments=6
    )
    assert willing.recommended_payment_method is RecommendedMethod.INSTALLMENTS
    assert unwilling.recommended_payment_method is not RecommendedMethod.INSTALLMENTS


def test_making_an_option_more_expensive_never_makes_it_preferable():
    cheap = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    dear = option("payment_option_03", PaymentMethod.INSTALLMENTS, "1200", 5, REQUEST_DATE, 10, "1000", "6000")
    both = recommend(
        options=(cheap, dear), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    assert both.selected_candidate_id == "installments:payment_option_02"


def test_stopping_a_flexible_expense_never_reduces_capacity():
    from buyorwait.evidence import FactType

    baseline = recommend(balance="9000", requested="100000")
    terminated = recommend(
        balance="9000",
        requested="100000",
        evidence=evidence_bundle(fact(FactType.EXPENSE_TERMINATED, effective=REQUEST_DATE, category="streaming")),
    )
    assert terminated.amount_safe_to_pay >= baseline.amount_safe_to_pay


def test_shuffling_the_production_event_file_does_not_change_any_output_row(monkeypatch):
    original = ingestion._read_rows

    def shuffled(path):
        rows = original(path)
        if path.name == ingestion.EVENTS_FILE:
            rows = list(rows)
            random.Random(3).shuffle(rows)
        return rows

    baseline = run_pipeline(DATASET_DIR, None)
    monkeypatch.setattr(ingestion, "_read_rows", shuffled)
    reordered = run_pipeline(DATASET_DIR, None)
    assert [r.record for r in baseline.results] == [r.record for r in reordered.results]
