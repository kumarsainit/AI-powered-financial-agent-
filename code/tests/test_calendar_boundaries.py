from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.domain import Cadence, EventType, RecurrenceStrength, RecurringEventCandidate
from buyorwait.financial_state import ForecastWindow
from buyorwait.projection import add_months, elapsed_cycles, is_stale, projected_dates


def _monthly_candidate(last_observed: date, occurrences: tuple[date, ...] | None = None) -> RecurringEventCandidate:
    dates = occurrences or (last_observed,)
    return RecurringEventCandidate(
        user_id="user_test",
        category="rent",
        event_type=EventType.EXPENSE,
        source_event_ids=tuple(f"e{i}" for i in range(len(dates))),
        occurrence_count=len(dates),
        observed_dates=dates,
        observed_intervals_days=(30,) * (len(dates) - 1),
        amounts=(Decimal("1000"),) * len(dates),
        currency="USD",
        inferred_cadence=Cadence.MONTHLY,
        strength=RecurrenceStrength.STRONG,
        reason="test fixture",
    )


def test_add_months_from_january_31_lands_on_february_28_in_a_non_leap_year():
    assert add_months(date(2025, 1, 31), 1, 31) == date(2025, 2, 28)


def test_add_months_from_january_31_lands_on_february_29_in_a_leap_year():
    assert add_months(date(2024, 1, 31), 1, 31) == date(2024, 2, 29)


def test_add_months_from_january_31_lands_on_march_31():
    assert add_months(date(2025, 1, 31), 2, 31) == date(2025, 3, 31)


def test_add_months_preserves_a_day_of_month_within_twenty_eight():
    assert add_months(date(2025, 1, 15), 1, 15) == date(2025, 2, 15)


def test_elapsed_cycles_across_a_february_month_end_is_zero_before_the_anchor_day():
    candidate = _monthly_candidate(date(2025, 1, 31))
    assert elapsed_cycles(candidate, date(2025, 2, 15)) == 0.0


def test_elapsed_cycles_across_a_february_month_end_is_one_after_the_anchor_day():
    candidate = _monthly_candidate(date(2025, 1, 31))
    assert elapsed_cycles(candidate, date(2025, 3, 1)) == 1.0


def test_is_stale_treats_a_january_31_series_as_current_through_february():
    candidate = _monthly_candidate(date(2025, 1, 31))
    window = ForecastWindow(start_date=date(2025, 2, 15), horizon_days=90)
    assert is_stale(candidate, window, max_staleness_multiple=1.5) is False


def test_is_stale_drops_a_january_31_series_once_far_enough_past_a_leap_february():
    candidate = _monthly_candidate(date(2024, 1, 31))
    window = ForecastWindow(start_date=date(2024, 5, 15), horizon_days=90)
    assert is_stale(candidate, window, max_staleness_multiple=1.5) is True


def test_projected_dates_from_january_31_yields_february_28_then_march_31_in_a_non_leap_year():
    candidate = _monthly_candidate(date(2025, 1, 31))
    window = ForecastWindow(start_date=date(2025, 2, 1), horizon_days=90)
    dates = projected_dates(candidate, window)
    assert date(2025, 2, 28) in dates
    assert date(2025, 3, 31) in dates
    assert all(d.month != 2 or d.day <= 28 for d in dates)


def test_projected_dates_from_january_31_yields_february_29_in_a_leap_year():
    candidate = _monthly_candidate(date(2024, 1, 31))
    window = ForecastWindow(start_date=date(2024, 2, 1), horizon_days=90)
    dates = projected_dates(candidate, window)
    assert date(2024, 2, 29) in dates


def test_projected_dates_around_february_never_produce_an_invalid_calendar_date():
    candidate = _monthly_candidate(date(2025, 1, 30))
    window = ForecastWindow(start_date=date(2025, 1, 30), horizon_days=90)
    dates = projected_dates(candidate, window)
    for projected in dates:
        date(projected.year, projected.month, projected.day)
