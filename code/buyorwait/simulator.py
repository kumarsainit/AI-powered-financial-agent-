from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from .domain import Direction
from .financial_state import (
    DailyFinancialState,
    ForecastWindow,
    ProjectedCashFlowEvent,
    SpendingClass,
)

_DIRECTION_ORDER = {Direction.CREDIT: 0, Direction.DEBIT: 1, Direction.NON_CASH: 2}


def ordering_key(event: ProjectedCashFlowEvent) -> tuple:
    return (
        event.when,
        _DIRECTION_ORDER[event.direction],
        event.kind.value,
        event.event_id,
    )


def simulate(
    opening_balance: Decimal,
    events: Iterable[ProjectedCashFlowEvent],
    window: ForecastWindow,
    minimum_balance: Decimal,
) -> tuple[DailyFinancialState, ...]:
    eligible_events = (
        event for event in events if event.amount_home is not None and window.contains(event.when)
    )
    by_day: dict = defaultdict(list)
    for event in sorted(eligible_events, key=ordering_key):
        by_day[event.when].append(event)

    balance = opening_balance
    states: list[DailyFinancialState] = []
    for day_index in range(window.horizon_days + 1):
        current = window.start_date + timedelta(days=day_index)
        day_events = by_day.get(current, ())
        opening = balance
        low = balance
        income = Decimal("0")
        essential = Decimal("0")
        flexible = Decimal("0")
        applied_ids: list[str] = []

        for event in day_events:
            amount = event.amount_home or Decimal("0")
            if event.direction is Direction.CREDIT:
                balance += amount
                income += amount
            elif event.direction is Direction.DEBIT:
                balance -= amount
                if event.spending_class is SpendingClass.FLEXIBLE:
                    flexible += amount
                else:
                    essential += amount
            applied_ids.append(event.event_id)
            if balance < low:
                low = balance

        states.append(
            DailyFinancialState(
                day_index=day_index,
                when=current,
                opening_balance=opening,
                income_applied=income,
                essential_expense_applied=essential,
                flexible_expense_applied=flexible,
                closing_balance=balance,
                intraday_low_balance=low,
                margin_to_minimum=balance - minimum_balance,
                breaches_minimum=balance < minimum_balance,
                applied_event_ids=tuple(applied_ids),
            )
        )

    return tuple(states)
