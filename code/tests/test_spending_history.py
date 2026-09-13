from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from buyorwait.domain import Direction, EventStatus, EventType, FinancialEvent, Flexibility
from buyorwait.spending_history import SpendingHistoryConfig, compute_spending_observations


def make_event(event_id: str, event_date: date, amount: str, status: EventStatus = EventStatus.SETTLED, direction: Direction = Direction.DEBIT) -> FinancialEvent:
    return FinancialEvent(
        event_id=event_id,
        user_id="user_test",
        event_type=EventType.EXPENSE,
        description="test",
        category="groceries",
        direction=direction,
        amount=Decimal(amount),
        currency="USD",
        event_date=event_date,
        settlement_date=event_date,
        status=status,
        linked_event_id=None,
        flexibility=Flexibility.FIXED,
        minimum_allowed_amount=None,
    )


class SpendingHistoryTests(unittest.TestCase):
    def test_insufficient_history_returns_no_observation(self) -> None:
        observations = compute_spending_observations([])
        self.assertEqual(observations, ())

    def test_single_observation_has_no_frequency(self) -> None:
        events = [make_event("e1", date(2024, 1, 1), "100")]
        observations = compute_spending_observations(events)
        self.assertEqual(len(observations), 1)
        self.assertIsNone(observations[0].spending_frequency_days)
        self.assertEqual(observations[0].occurrence_count, 1)

    def test_average_median_maximum(self) -> None:
        amounts = ["100", "200", "300", "400"]
        events = [make_event(f"e{i}", date(2024, 1, 1 + i * 7), amount) for i, amount in enumerate(amounts)]
        observations = compute_spending_observations(events)
        obs = observations[0]
        self.assertEqual(obs.historical_total, Decimal("1000"))
        self.assertEqual(obs.historical_average, Decimal("250"))
        self.assertEqual(obs.historical_median, Decimal("250"))
        self.assertEqual(obs.historical_maximum, Decimal("400"))

    def test_recent_observations_use_configured_window(self) -> None:
        amounts = ["100", "500", "100", "300", "700"]
        events = [make_event(f"e{i}", date(2024, 1, 1 + i * 7), amount) for i, amount in enumerate(amounts)]
        observations = compute_spending_observations(events, SpendingHistoryConfig(recent_window=2))
        obs = observations[0]
        self.assertEqual(obs.recent_average, Decimal("500"))
        self.assertEqual(obs.recent_maximum, Decimal("700"))

    def test_individual_observations_are_retained(self) -> None:
        events = [make_event("e1", date(2024, 1, 1), "100"), make_event("e2", date(2024, 1, 8), "200")]
        observations = compute_spending_observations(events)
        self.assertEqual(len(observations[0].observations), 2)
        self.assertEqual(observations[0].observations[0], (date(2024, 1, 1), Decimal("100")))

    def test_credit_events_are_not_spending(self) -> None:
        events = [make_event("e1", date(2024, 1, 1), "100", direction=Direction.CREDIT)]
        observations = compute_spending_observations(events)
        self.assertEqual(observations, ())

    def test_pending_events_are_not_historical_spending(self) -> None:
        events = [make_event("e1", date(2024, 1, 1), "100", status=EventStatus.PENDING)]
        observations = compute_spending_observations(events)
        self.assertEqual(observations, ())

    def test_blank_amount_events_are_excluded_not_zeroed(self) -> None:
        event = FinancialEvent(
            event_id="e1", user_id="user_test", event_type=EventType.EXPENSE, description="test",
            category="groceries", direction=Direction.DEBIT, amount=None, currency="USD",
            event_date=date(2024, 1, 1), settlement_date=date(2024, 1, 1), status=EventStatus.SETTLED,
            linked_event_id=None, flexibility=Flexibility.FIXED, minimum_allowed_amount=None,
        )
        observations = compute_spending_observations([event])
        self.assertEqual(observations, ())


if __name__ == "__main__":
    unittest.main()
