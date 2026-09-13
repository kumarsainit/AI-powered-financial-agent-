from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.bundle import build_request_bundle
from buyorwait.domain import EventStatus
from buyorwait.evidence_pipeline import build_evidence_bundle
from buyorwait.forecast import build_financial_state
from buyorwait.ingestion import load_dataset, load_sample_request_user_ids
from buyorwait.invariants import check_forecast_invariants
from buyorwait.usage import UsageTracker
from tests.paths import DATASET_DIR

SAMPLE_REQUEST_IDS = ("request_26", "request_60", "request_119", "request_154", "request_200")


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATASET_DIR)


@pytest.fixture(scope="module")
def tracker():
    return UsageTracker()


def forecast_for(dataset, tracker, request_id):
    bundle = build_request_bundle(dataset, request_id)
    evidence = build_evidence_bundle(bundle, tracker)
    return build_financial_state(bundle, evidence)


@pytest.mark.parametrize("request_id", SAMPLE_REQUEST_IDS)
def test_production_requests_build_a_ninety_one_day_forecast(dataset, tracker, request_id):
    forecast = forecast_for(dataset, tracker, request_id)
    assert len(forecast.daily_states) == 91
    assert forecast.window.end_date == forecast.window.start_date + timedelta(days=90)
    check_forecast_invariants(forecast)


@pytest.mark.parametrize("request_id", SAMPLE_REQUEST_IDS)
def test_no_projected_event_predates_request_date(dataset, tracker, request_id):
    forecast = forecast_for(dataset, tracker, request_id)
    assert all(event.when >= forecast.window.start_date for event in forecast.events)


@pytest.mark.parametrize("request_id", SAMPLE_REQUEST_IDS)
def test_no_future_settled_ledger_row_enters_the_forecast(dataset, tracker, request_id):
    bundle = build_request_bundle(dataset, request_id)
    forecast = forecast_for(dataset, tracker, request_id)
    settled_future = {
        event.event_id
        for event in bundle.events
        if event.status is EventStatus.SETTLED and event.event_date >= bundle.request.request_date
    }
    used_sources = {source for event in forecast.events for source in event.source_event_ids}
    assert not (settled_future & used_sources)


def test_sample_users_never_enter_a_production_forecast(dataset):
    sample_users = load_sample_request_user_ids(DATASET_DIR)
    assert not (set(dataset.profiles) & sample_users)
    for request_id in SAMPLE_REQUEST_IDS:
        bundle = build_request_bundle(dataset, request_id)
        assert bundle.profile.user_id not in sample_users
        assert all(event.user_id not in sample_users for event in bundle.events)


@pytest.mark.parametrize("request_id", SAMPLE_REQUEST_IDS)
def test_repeated_production_simulation_is_identical(dataset, tracker, request_id):
    first = forecast_for(dataset, tracker, request_id)
    second = forecast_for(dataset, tracker, request_id)
    assert first.daily_states == second.daily_states
    assert first.events == second.events
    assert first.minimum_projected_balance == second.minimum_projected_balance


@pytest.mark.parametrize("request_id", SAMPLE_REQUEST_IDS)
def test_minimum_balance_covers_the_entire_window(dataset, tracker, request_id):
    forecast = forecast_for(dataset, tracker, request_id)
    assert forecast.minimum_projected_balance == min(
        state.intraday_low_balance for state in forecast.daily_states
    )
    assert isinstance(forecast.minimum_projected_balance, Decimal)


def test_unresolved_amount_requests_never_gain_an_invented_amount(dataset, tracker):
    forecast = forecast_for(dataset, tracker, "request_119")
    assert forecast.unresolved_obligations
    assert all(
        obligation.reference_id not in {event.event_id for event in forecast.events}
        for obligation in forecast.unresolved_obligations
    )
