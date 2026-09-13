from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from buyorwait.domain import Cadence, Direction, EventStatus, EventType, FinancialEvent, Flexibility, RecurrenceStrength
from buyorwait.recurrence import RecurrenceDetectorConfig, detect_recurrence


def make_event(event_id: str, event_date: date, category: str = "rent", amount: str = "1000", status: EventStatus = EventStatus.SETTLED) -> FinancialEvent:
    return FinancialEvent(
        event_id=event_id,
        user_id="user_test",
        event_type=EventType.EXPENSE,
        description="test",
        category=category,
        direction=Direction.DEBIT,
        amount=Decimal(amount),
        currency="USD",
        event_date=event_date,
        settlement_date=event_date,
        status=status,
        linked_event_id=None,
        flexibility=Flexibility.FIXED,
        minimum_allowed_amount=None,
    )


def dates_from(start: date, gaps: list[int]) -> list[date]:
    current = start
    result = [current]
    for gap in gaps:
        current = current + timedelta(days=gap)
        result.append(current)
    return result


class RecurrenceTests(unittest.TestCase):
    def test_one_off_event(self) -> None:
        events = [make_event("e1", date(2024, 1, 1))]
        candidates = detect_recurrence(events)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].strength, RecurrenceStrength.ONE_OFF)
        self.assertEqual(candidates[0].occurrence_count, 1)

    def test_exactly_two_occurrences_meets_default_threshold(self) -> None:
        dates = dates_from(date(2024, 1, 1), [30])
        events = [make_event(f"e{i}", d) for i, d in enumerate(dates)]
        candidates = detect_recurrence(events)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].occurrence_count, 2)
        self.assertNotEqual(candidates[0].strength, RecurrenceStrength.ONE_OFF)

    def test_three_or_more_occurrences_consistent_monthly(self) -> None:
        dates = dates_from(date(2024, 1, 2), [30, 31, 30])
        events = [make_event(f"e{i}", d) for i, d in enumerate(dates)]
        candidates = detect_recurrence(events)
        self.assertEqual(candidates[0].strength, RecurrenceStrength.STRONG)
        self.assertEqual(candidates[0].inferred_cadence, Cadence.MONTHLY)
        self.assertEqual(candidates[0].occurrence_count, 4)

    def test_consistent_weekly_interval(self) -> None:
        dates = dates_from(date(2024, 1, 1), [7, 7, 7, 7])
        events = [make_event(f"e{i}", d, category="groceries") for i, d in enumerate(dates)]
        candidates = detect_recurrence(events)
        self.assertEqual(candidates[0].strength, RecurrenceStrength.STRONG)
        self.assertEqual(candidates[0].inferred_cadence, Cadence.WEEKLY)

    def test_inconsistent_intervals_are_weak(self) -> None:
        dates = dates_from(date(2024, 1, 1), [2, 25, 3, 22])
        events = [make_event(f"e{i}", d, category="dining") for i, d in enumerate(dates)]
        candidates = detect_recurrence(events)
        self.assertEqual(candidates[0].strength, RecurrenceStrength.WEAK)
        self.assertEqual(candidates[0].inferred_cadence, Cadence.NONE)

    def test_repeated_but_non_recurring_looking_events(self) -> None:
        dates = dates_from(date(2024, 1, 1), [1, 45, 2, 60, 1])
        events = [make_event(f"e{i}", d, category="shopping") for i, d in enumerate(dates)]
        candidates = detect_recurrence(events)
        self.assertEqual(candidates[0].strength, RecurrenceStrength.WEAK)

    def test_configurable_minimum_occurrences(self) -> None:
        dates = dates_from(date(2024, 1, 1), [30])
        events = [make_event(f"e{i}", d) for i, d in enumerate(dates)]
        strict_config = RecurrenceDetectorConfig(minimum_occurrences=3)
        candidates = detect_recurrence(events, strict_config)
        self.assertEqual(candidates[0].strength, RecurrenceStrength.ONE_OFF)

    def test_pending_and_cancelled_events_are_excluded_from_pattern_learning(self) -> None:
        dates = dates_from(date(2024, 1, 1), [30, 30])
        events = [make_event(f"e{i}", d) for i, d in enumerate(dates)]
        events.append(make_event("pending", date(2024, 4, 1), status=EventStatus.PENDING))
        events.append(make_event("cancelled", date(2024, 5, 1), status=EventStatus.CANCELLED))
        candidates = detect_recurrence(events)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].occurrence_count, 3)

    def test_reason_is_populated(self) -> None:
        events = [make_event("e1", date(2024, 1, 1))]
        candidates = detect_recurrence(events)
        self.assertTrue(candidates[0].reason)

    def test_different_categories_are_independent_candidates(self) -> None:
        rent_dates = dates_from(date(2024, 1, 2), [30, 31])
        events = [make_event(f"rent{i}", d, category="rent") for i, d in enumerate(rent_dates)]
        events.append(make_event("groceries0", date(2024, 1, 1), category="groceries"))
        candidates = detect_recurrence(events)
        categories = {c.category for c in candidates}
        self.assertEqual(categories, {"rent", "groceries"})


if __name__ == "__main__":
    unittest.main()
