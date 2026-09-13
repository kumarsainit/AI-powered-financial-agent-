from __future__ import annotations

import statistics
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Sequence

from .currency import ExchangeRateTable
from .domain import (
    Cadence,
    Direction,
    EventType,
    FinancialEvent,
    Flexibility,
    RecurrenceStrength,
    RecurringEventCandidate,
    UserFinancialProfile,
)
from .errors import ExchangeRateNotFoundError
from .estimators import AmountStatistic, estimate_amount
from .financial_state import (
    CashFlowKind,
    Certainty,
    ConversionStatus,
    ForecastWindow,
    ProjectedCashFlowEvent,
    Provenance,
    ProvenanceKind,
    SpendingClass,
)
from .forecast_config import ForecastConfig

_INCOME_TYPES = (EventType.INCOME,)
_MAX_PROJECTED_OCCURRENCES = 200


def add_months(anchor: date, months: int, day_of_month: int) -> date:
    total = anchor.year * 12 + (anchor.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = day_of_month
    while day > 28:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, day)


def cadence_step_days(candidate: RecurringEventCandidate) -> int | None:
    if candidate.inferred_cadence is Cadence.WEEKLY:
        return 7
    if candidate.inferred_cadence is Cadence.BIWEEKLY:
        return 14
    if candidate.observed_intervals_days:
        step = int(round(statistics.mean(candidate.observed_intervals_days)))
        return step if step > 0 else None
    return None


def _staleness_applies(direction: Direction, scope: str) -> bool:
    if scope == "all":
        return True
    if scope == "income":
        return direction is Direction.CREDIT
    if scope == "expense":
        return direction is Direction.DEBIT
    return False


def elapsed_cycles(candidate: RecurringEventCandidate, reference: date) -> float | None:
    last_observed = candidate.observed_dates[-1]
    if candidate.inferred_cadence is Cadence.MONTHLY:
        months = (reference.year - last_observed.year) * 12 + (reference.month - last_observed.month)
        if reference.day < last_observed.day:
            months -= 1
        return float(months)
    step = cadence_step_days(candidate)
    if not step:
        return None
    return (reference - last_observed).days / step


def is_stale(
    candidate: RecurringEventCandidate,
    window: ForecastWindow,
    max_staleness_multiple: float,
) -> bool:
    if not candidate.observed_dates:
        return True
    if candidate.observed_dates[-1] >= window.start_date:
        return False
    cycles = elapsed_cycles(candidate, window.start_date)
    if cycles is None:
        return False
    return cycles > max_staleness_multiple


def projected_dates(candidate: RecurringEventCandidate, window: ForecastWindow) -> tuple[date, ...]:
    if not candidate.observed_dates:
        return ()
    last_observed = candidate.observed_dates[-1]
    results: list[date] = []

    if candidate.inferred_cadence is Cadence.MONTHLY:
        day_of_month = last_observed.day
        step = 1
        while step <= _MAX_PROJECTED_OCCURRENCES:
            candidate_date = add_months(last_observed, step, day_of_month)
            if candidate_date > window.end_date:
                break
            if candidate_date >= window.start_date:
                results.append(candidate_date)
            step += 1
        return tuple(results)

    step_days = cadence_step_days(candidate)
    if step_days is None:
        return ()
    current = last_observed
    for _ in range(_MAX_PROJECTED_OCCURRENCES):
        current = current + timedelta(days=step_days)
        if current > window.end_date:
            break
        if current >= window.start_date:
            results.append(current)
    return tuple(results)


def classify_spending(
    candidate_events: Sequence[FinancialEvent],
    profile: UserFinancialProfile,
    direction: Direction,
) -> SpendingClass:
    if direction is Direction.CREDIT:
        return SpendingClass.INCOME
    flexibilities = {event.flexibility for event in candidate_events}
    if flexibilities and flexibilities <= {Flexibility.FIXED}:
        return SpendingClass.ESSENTIAL
    return SpendingClass.FLEXIBLE


def select_statistic(
    spending_class: SpendingClass,
    amounts: Sequence[Decimal],
    config: ForecastConfig,
) -> AmountStatistic:
    if spending_class is SpendingClass.INCOME:
        return config.income_statistic
    if len(amounts) > 1:
        mean_amount = sum(amounts, start=Decimal("0")) / Decimal(len(amounts))
        if mean_amount > 0:
            spread = (max(amounts) - min(amounts)) / mean_amount
            if spread <= Decimal(str(config.fixed_amount_tolerance)):
                return config.fixed_expense_statistic
    if spending_class is SpendingClass.FLEXIBLE:
        return config.flexible_expense_statistic
    return config.variable_essential_expense_statistic


def _convert(
    amount: Decimal,
    when: date,
    from_currency: str,
    to_currency: str,
    rates: ExchangeRateTable,
) -> tuple[Decimal | None, ConversionStatus]:
    if from_currency == to_currency:
        return amount, ConversionStatus.SAME_CURRENCY
    try:
        return rates.convert(amount, when, from_currency, to_currency), ConversionStatus.CONVERTED
    except ExchangeRateNotFoundError:
        return None, ConversionStatus.UNRESOLVED_RATE


def project_recurrence(
    candidates: Iterable[RecurringEventCandidate],
    events_by_id: dict[str, FinancialEvent],
    profile: UserFinancialProfile,
    window: ForecastWindow,
    rates: ExchangeRateTable,
    config: ForecastConfig,
) -> tuple[ProjectedCashFlowEvent, ...]:
    projected: list[ProjectedCashFlowEvent] = []

    for candidate in sorted(candidates, key=lambda c: (c.category, c.source_event_ids)):
        if candidate.strength is RecurrenceStrength.ONE_OFF:
            continue
        source_events = [events_by_id[event_id] for event_id in candidate.source_event_ids if event_id in events_by_id]
        if not source_events:
            continue
        directions = {event.direction for event in source_events}
        if len(directions) != 1:
            continue
        (direction,) = directions
        if direction is Direction.NON_CASH:
            continue
        if _staleness_applies(direction, config.staleness_scope) and is_stale(
            candidate, window, config.max_staleness_multiple
        ):
            continue

        amounts = [event.amount for event in source_events if event.amount is not None]
        if not amounts:
            continue

        spending_class = classify_spending(source_events, profile, direction)
        is_income = source_events[0].event_type in _INCOME_TYPES or direction is Direction.CREDIT

        if candidate.strength is RecurrenceStrength.WEAK:
            if is_income and not config.project_weak_income:
                continue
            if not is_income and not config.project_weak_expenses:
                continue
            certainty = Certainty.PROJECTED_WEAK
        else:
            certainty = Certainty.PROJECTED_STRONG

        statistic = select_statistic(spending_class, amounts, config)
        estimated = estimate_amount(amounts, statistic, config.recent_window)

        dates = projected_dates(candidate, window)
        category = candidate.category
        is_protected = category in profile.expense_categories_to_protect
        permits_reduce = category in profile.expense_categories_user_is_willing_to_reduce
        permits_stop = category in profile.expense_categories_user_is_willing_to_stop
        flexibilities = {event.flexibility for event in source_events}
        reducible = permits_reduce and bool(
            flexibilities & {Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE}
        )
        stoppable = permits_stop and bool(
            flexibilities & {Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE}
        )

        for index, when in enumerate(dates, start=1):
            amount_home, conversion_status = _convert(
                estimated, when, candidate.currency, profile.home_currency, rates
            )
            projected.append(
                ProjectedCashFlowEvent(
                    event_id=f"proj:{candidate.category}:{candidate.source_event_ids[-1]}:{index}",
                    kind=CashFlowKind.PROJECTED_RECURRING
                    if spending_class is not SpendingClass.FLEXIBLE
                    else CashFlowKind.VARIABLE_BASELINE,
                    certainty=certainty,
                    spending_class=spending_class,
                    when=when,
                    direction=direction,
                    original_amount=estimated,
                    original_currency=candidate.currency,
                    amount_home=amount_home,
                    home_currency=profile.home_currency,
                    conversion_status=conversion_status,
                    category=category,
                    description=f"projected {candidate.category} ({candidate.inferred_cadence.value})",
                    provenance=Provenance(
                        kind=ProvenanceKind.RECURRENCE_PATTERN,
                        reference_id="|".join(candidate.source_event_ids),
                        detail=(
                            f"{candidate.strength.value} recurrence, cadence={candidate.inferred_cadence.value}, "
                            f"amount_statistic={statistic.value}, occurrences={candidate.occurrence_count}"
                        ),
                    ),
                    source_cadence=candidate.inferred_cadence,
                    source_event_ids=candidate.source_event_ids,
                    is_reducible=reducible,
                    is_stoppable=stoppable,
                    is_protected=is_protected,
                )
            )

    return tuple(projected)
