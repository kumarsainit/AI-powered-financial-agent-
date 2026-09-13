from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .domain import Cadence, EventStatus, FinancialEvent, RecurrenceStrength, RecurringEventCandidate


@dataclass(frozen=True)
class RecurrenceDetectorConfig:
    minimum_occurrences: int = 2
    weekly_days: tuple[int, int] = (5, 9)
    biweekly_days: tuple[int, int] = (12, 16)
    monthly_days: tuple[int, int] = (27, 33)
    max_coefficient_of_variation: float = 0.35


_ELIGIBLE_STATUSES = (EventStatus.SETTLED, EventStatus.SCHEDULED)


def _classify_cadence(mean_interval_days: float, config: RecurrenceDetectorConfig) -> Cadence:
    if config.weekly_days[0] <= mean_interval_days <= config.weekly_days[1]:
        return Cadence.WEEKLY
    if config.biweekly_days[0] <= mean_interval_days <= config.biweekly_days[1]:
        return Cadence.BIWEEKLY
    if config.monthly_days[0] <= mean_interval_days <= config.monthly_days[1]:
        return Cadence.MONTHLY
    return Cadence.OTHER


def detect_recurrence(
    events: Iterable[FinancialEvent],
    config: RecurrenceDetectorConfig = RecurrenceDetectorConfig(),
) -> tuple[RecurringEventCandidate, ...]:
    groups: dict[tuple[str, str], list[FinancialEvent]] = defaultdict(list)
    for event in events:
        if event.status in _ELIGIBLE_STATUSES:
            groups[(event.user_id, event.category)].append(event)

    candidates: list[RecurringEventCandidate] = []
    for (user_id, category), group in groups.items():
        ordered = sorted(group, key=lambda e: e.event_date)
        dates = tuple(e.event_date for e in ordered)
        amounts = tuple(e.amount for e in ordered if e.amount is not None)
        source_event_ids = tuple(e.event_id for e in ordered)
        event_type = ordered[0].event_type
        currency = ordered[0].currency
        count = len(ordered)

        if count < config.minimum_occurrences:
            candidates.append(
                RecurringEventCandidate(
                    user_id=user_id,
                    category=category,
                    event_type=event_type,
                    source_event_ids=source_event_ids,
                    occurrence_count=count,
                    observed_dates=dates,
                    observed_intervals_days=(),
                    amounts=amounts,
                    currency=currency,
                    inferred_cadence=Cadence.NONE,
                    strength=RecurrenceStrength.ONE_OFF,
                    reason=(
                        f"only {count} occurrence(s) observed, "
                        f"below the configured minimum of {config.minimum_occurrences}"
                    ),
                )
            )
            continue

        intervals = tuple((dates[i + 1] - dates[i]).days for i in range(len(dates) - 1))
        mean_interval = statistics.mean(intervals)
        stdev_interval = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
        coefficient_of_variation = (stdev_interval / mean_interval) if mean_interval else float("inf")

        if coefficient_of_variation <= config.max_coefficient_of_variation:
            cadence = _classify_cadence(mean_interval, config)
            strength = RecurrenceStrength.STRONG
            reason = (
                f"{count} occurrences at a mean interval of {mean_interval:.1f} days "
                f"(coefficient of variation {coefficient_of_variation:.2f} <= {config.max_coefficient_of_variation})"
            )
        else:
            cadence = Cadence.NONE
            strength = RecurrenceStrength.WEAK
            reason = (
                f"{count} occurrences but irregular spacing "
                f"(coefficient of variation {coefficient_of_variation:.2f} > {config.max_coefficient_of_variation})"
            )

        candidates.append(
            RecurringEventCandidate(
                user_id=user_id,
                category=category,
                event_type=event_type,
                source_event_ids=source_event_ids,
                occurrence_count=count,
                observed_dates=dates,
                observed_intervals_days=intervals,
                amounts=amounts,
                currency=currency,
                inferred_cadence=cadence,
                strength=strength,
                reason=reason,
            )
        )

    return tuple(candidates)
