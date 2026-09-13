from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from buyorwait.domain import (
    Direction,
    EventStatus,
    EventType,
    FinancialEvent,
    Flexibility,
    LifecyclePattern,
    MemberDisposition,
)
from buyorwait.lifecycle import resolve_lifecycle


def make_event(
    event_id: str,
    event_type: EventType,
    status: EventStatus,
    direction: Direction = Direction.DEBIT,
    linked_event_id: str | None = None,
    category: str = "shopping",
    amount: str = "100",
) -> FinancialEvent:
    return FinancialEvent(
        event_id=event_id,
        user_id="user_test",
        event_type=event_type,
        description="test event",
        category=category,
        direction=direction,
        amount=Decimal(amount),
        currency="USD",
        event_date=date(2024, 1, 1),
        settlement_date=date(2024, 1, 2),
        status=status,
        linked_event_id=linked_event_id,
        flexibility=Flexibility.FIXED,
        minimum_allowed_amount=None,
    )


def find_chain(chains, event_id):
    for chain in chains:
        if any(m.event_id == event_id for m in chain.members):
            return chain
    raise AssertionError(f"no chain found for {event_id}")


class LifecycleTests(unittest.TestCase):
    def test_standalone_event(self) -> None:
        events = [make_event("e1", EventType.EXPENSE, EventStatus.SETTLED)]
        chains = resolve_lifecycle(events)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0].pattern, LifecyclePattern.STANDALONE)
        self.assertEqual(chains[0].included_event_ids(), ("e1",))

    def test_refund_settled(self) -> None:
        events = [
            make_event("refund", EventType.REFUND, EventStatus.SETTLED, direction=Direction.CREDIT, linked_event_id="orig"),
            make_event("orig", EventType.EXPENSE, EventStatus.SETTLED),
        ]
        chains = resolve_lifecycle(events)
        chain = find_chain(chains, "refund")
        self.assertEqual(chain.pattern, LifecyclePattern.REFUND_SETTLED)
        self.assertEqual(set(chain.included_event_ids()), {"refund", "orig"})

    def test_cancelled_authorization(self) -> None:
        events = [
            make_event("settled", EventType.EXPENSE, EventStatus.SETTLED, linked_event_id="cancelled"),
            make_event("cancelled", EventType.EXPENSE, EventStatus.CANCELLED),
        ]
        chains = resolve_lifecycle(events)
        chain = find_chain(chains, "settled")
        self.assertEqual(chain.pattern, LifecyclePattern.CANCELLED_AUTHORIZATION)
        self.assertEqual(chain.included_event_ids(), ("settled",))
        self.assertEqual(chain.excluded_event_ids(), ("cancelled",))

    def test_failed_then_retried(self) -> None:
        events = [
            make_event("retry", EventType.DEBT_PAYMENT, EventStatus.SCHEDULED, linked_event_id="failed"),
            make_event("failed", EventType.DEBT_PAYMENT, EventStatus.FAILED),
        ]
        chains = resolve_lifecycle(events)
        chain = find_chain(chains, "retry")
        self.assertEqual(chain.pattern, LifecyclePattern.FAILED_RETRY)
        self.assertEqual(chain.included_event_ids(), ("retry",))
        self.assertEqual(chain.excluded_event_ids(), ("failed",))

    def test_possible_duplicate(self) -> None:
        events = [
            make_event("pending_dup", EventType.EXPENSE, EventStatus.PENDING, linked_event_id="original"),
            make_event("original", EventType.EXPENSE, EventStatus.SETTLED),
        ]
        chains = resolve_lifecycle(events)
        chain = find_chain(chains, "pending_dup")
        self.assertEqual(chain.pattern, LifecyclePattern.POSSIBLE_DUPLICATE)
        self.assertEqual(chain.included_event_ids(), ("original",))
        self.assertEqual(chain.excluded_event_ids(), ("pending_dup",))

    def test_linked_event_chain_never_double_counts(self) -> None:
        events = [
            make_event("pending_dup", EventType.EXPENSE, EventStatus.PENDING, linked_event_id="original", amount="500"),
            make_event("original", EventType.EXPENSE, EventStatus.SETTLED, amount="500"),
        ]
        chains = resolve_lifecycle(events)
        included_ids = {eid for chain in chains for eid in chain.included_event_ids()}
        self.assertEqual(included_ids, {"original"})

    def test_unclassified_link_falls_back_to_include(self) -> None:
        events = [
            make_event("a", EventType.EXPENSE, EventStatus.SETTLED, linked_event_id="b"),
            make_event("b", EventType.INCOME, EventStatus.SETTLED),
        ]
        chains = resolve_lifecycle(events)
        chain = find_chain(chains, "a")
        self.assertEqual(chain.pattern, LifecyclePattern.UNCLASSIFIED_LINK)
        self.assertEqual(set(chain.included_event_ids()), {"a", "b"})

    def test_dangling_linked_event_id_is_treated_as_standalone(self) -> None:
        events = [make_event("a", EventType.EXPENSE, EventStatus.SETTLED, linked_event_id="missing")]
        chains = resolve_lifecycle(events)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0].pattern, LifecyclePattern.STANDALONE)


if __name__ == "__main__":
    unittest.main()
