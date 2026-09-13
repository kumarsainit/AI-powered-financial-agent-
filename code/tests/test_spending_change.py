from __future__ import annotations

import dataclasses
import unittest
from datetime import date
from decimal import Decimal

from buyorwait.domain import Direction, EventStatus, EventType, FinancialEvent, Flexibility, PaymentMethod, UserFinancialProfile
from buyorwait.spending_change import build_spending_change_candidates


def make_profile(**overrides) -> UserFinancialProfile:
    defaults = dict(
        user_id="user_test",
        home_currency="USD",
        current_available_balance=Decimal("1000"),
        minimum_balance_to_keep=Decimal("100"),
        financial_priorities=(),
        expense_categories_to_protect=("rent",),
        expense_categories_user_is_willing_to_reduce=("dining",),
        expense_categories_user_is_willing_to_stop=("streaming",),
        payment_methods_user_will_consider=(PaymentMethod.FULL_PAYMENT,),
        max_installment_months=None,
    )
    defaults.update(overrides)
    return UserFinancialProfile(**defaults)


def make_event(event_id: str, category: str, flexibility: Flexibility, minimum_allowed_amount: str | None = None, amount: str = "100", status: EventStatus = EventStatus.SETTLED) -> FinancialEvent:
    return FinancialEvent(
        event_id=event_id, user_id="user_test", event_type=EventType.EXPENSE, description="test",
        category=category, direction=Direction.DEBIT, amount=Decimal(amount), currency="USD",
        event_date=date(2024, 1, 1), settlement_date=date(2024, 1, 1), status=status,
        linked_event_id=None, flexibility=flexibility,
        minimum_allowed_amount=Decimal(minimum_allowed_amount) if minimum_allowed_amount else None,
    )


class SpendingChangeTests(unittest.TestCase):
    def test_fixed_events_are_never_candidates(self) -> None:
        events = [make_event("e1", "rent", Flexibility.FIXED)]
        candidates = build_spending_change_candidates(events, make_profile(), ())
        self.assertEqual(candidates, ())

    def test_reducible_event_carries_minimum_allowed_amount(self) -> None:
        events = [make_event("e1", "dining", Flexibility.REDUCIBLE, minimum_allowed_amount="40")]
        candidates = build_spending_change_candidates(events, make_profile(), ())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].minimum_allowed_amount, Decimal("40"))
        self.assertTrue(candidates[0].user_permits_reduce)

    def test_stoppable_event_reflects_stop_permission(self) -> None:
        events = [make_event("e1", "streaming", Flexibility.STOPPABLE)]
        candidates = build_spending_change_candidates(events, make_profile(), ())
        self.assertTrue(candidates[0].user_permits_stop)
        self.assertFalse(candidates[0].user_permits_reduce)

    def test_reducible_or_stoppable_event(self) -> None:
        events = [make_event("e1", "streaming", Flexibility.REDUCIBLE_OR_STOPPABLE, minimum_allowed_amount="10")]
        candidates = build_spending_change_candidates(events, make_profile(), ())
        self.assertEqual(candidates[0].flexibility, Flexibility.REDUCIBLE_OR_STOPPABLE)
        self.assertEqual(candidates[0].minimum_allowed_amount, Decimal("10"))

    def test_protected_category_is_flagged(self) -> None:
        events = [make_event("e1", "rent", Flexibility.REDUCIBLE, minimum_allowed_amount="500")]
        candidates = build_spending_change_candidates(events, make_profile(), ())
        self.assertTrue(candidates[0].is_protected)

    def test_pending_events_are_not_change_candidates(self) -> None:
        events = [make_event("e1", "dining", Flexibility.REDUCIBLE, status=EventStatus.PENDING)]
        candidates = build_spending_change_candidates(events, make_profile(), ())
        self.assertEqual(candidates, ())

    def test_event_from_wrong_user_raises(self) -> None:
        event = make_event("e1", "dining", Flexibility.REDUCIBLE)
        other_user_event = dataclasses.replace(event, user_id="someone_else")
        with self.assertRaises(Exception):
            build_spending_change_candidates([other_user_event], make_profile(), ())


if __name__ == "__main__":
    unittest.main()
