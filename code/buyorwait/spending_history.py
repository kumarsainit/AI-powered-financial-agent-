from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .domain import Direction, EventStatus, FinancialEvent, HistoricalSpendingObservation


@dataclass(frozen=True)
class SpendingHistoryConfig:
    recent_window: int = 3


def compute_spending_observations(
    events: Iterable[FinancialEvent],
    config: SpendingHistoryConfig = SpendingHistoryConfig(),
) -> tuple[HistoricalSpendingObservation, ...]:
    groups: dict[tuple[str, str], list[FinancialEvent]] = defaultdict(list)
    for event in events:
        if event.status is EventStatus.SETTLED and event.direction is Direction.DEBIT and event.amount is not None:
            groups[(event.user_id, event.category)].append(event)

    observations: list[HistoricalSpendingObservation] = []
    for (user_id, category), group in groups.items():
        ordered = sorted(group, key=lambda e: (e.event_date, e.event_id))
        dates = [e.event_date for e in ordered]
        amounts = [e.amount for e in ordered]
        count = len(ordered)

        total = sum(amounts, start=Decimal("0"))
        average = total / count
        median = statistics.median(amounts)
        maximum = max(amounts)

        recent = amounts[-config.recent_window :]
        recent_average = sum(recent, start=Decimal("0")) / len(recent)
        recent_maximum = max(recent)

        if count > 1:
            intervals = [(dates[i + 1] - dates[i]).days for i in range(count - 1)]
            frequency_days: float | None = statistics.mean(intervals)
        else:
            frequency_days = None

        observations.append(
            HistoricalSpendingObservation(
                user_id=user_id,
                category=category,
                currency=ordered[0].currency,
                observations=tuple(zip(dates, amounts)),
                historical_total=total,
                historical_average=average,
                historical_median=median,
                historical_maximum=maximum,
                recent_average=recent_average,
                recent_maximum=recent_maximum,
                occurrence_count=count,
                spending_frequency_days=frequency_days,
                observed_date_range=(dates[0], dates[-1]),
            )
        )

    return tuple(observations)
