from __future__ import annotations

from decimal import Decimal

from .bundle import RequestBundle
from .domain import Direction
from .evidence import EvidenceBundle
from .evidence_adjustments import apply_evidence
from .financial_state import (
    ConversionStatus,
    ExcludedCashFlow,
    ExclusionReason,
    FinancialStateForecast,
    ForecastWindow,
    ProjectedCashFlowEvent,
    SpendingClass,
    UnresolvedObligation,
)
from .forecast_config import ForecastConfig
from .invariants import check_forecast_invariants
from .projection import project_recurrence
from .reconstruction import build_confirmed_future_events, build_starting_state
from .simulator import ordering_key, simulate

_ZERO = Decimal("0")


def build_financial_state(
    bundle: RequestBundle,
    evidence: EvidenceBundle | None = None,
    config: ForecastConfig = ForecastConfig(),
    verify_invariants: bool = True,
) -> FinancialStateForecast:
    profile = bundle.profile
    window = ForecastWindow(start_date=bundle.request.request_date, horizon_days=config.horizon_days)
    rates = bundle.exchange_rates

    starting_state, ledger_unresolved = build_starting_state(bundle, rates)
    confirmed_events, confirmed_excluded, confirmed_unresolved = build_confirmed_future_events(bundle, window, rates)

    events_by_id = {event.event_id: event for event in bundle.events if event.event_id in bundle.included_event_ids}
    recurring_events = project_recurrence(
        bundle.recurrence_candidates, events_by_id, profile, window, rates, config
    )

    confirmed_source_ids = {
        source_id for event in confirmed_events for source_id in event.source_event_ids
    }
    deduplicated_recurring = tuple(
        event
        for event in recurring_events
        if not (
            event.spending_class is SpendingClass.INCOME
            and any(
                other.spending_class is SpendingClass.INCOME
                and other.category == event.category
                and other.when == event.when
                for other in confirmed_events
            )
        )
    )

    all_events = confirmed_events + deduplicated_recurring
    excluded: list[ExcludedCashFlow] = list(starting_state.excluded_items) + list(confirmed_excluded)
    unresolved: list[UnresolvedObligation] = list(ledger_unresolved) + list(confirmed_unresolved)
    notes: list[str] = []

    if confirmed_source_ids:
        notes.append(
            f"{len(confirmed_source_ids)} scheduled ledger row(s) entered the window as confirmed future events"
        )

    if evidence is not None:
        adjustment = apply_evidence(evidence, all_events, profile, window, rates)
        all_events = adjustment.events
        excluded.extend(adjustment.excluded)
        unresolved.extend(adjustment.unresolved)
        notes.extend(adjustment.notes)

    simulated_events: list[ProjectedCashFlowEvent] = []
    has_unresolved_conversion = False
    for event in all_events:
        if event.amount_home is None:
            has_unresolved_conversion = has_unresolved_conversion or (
                event.conversion_status is ConversionStatus.UNRESOLVED_RATE
            )
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=ExclusionReason.MISSING_EXCHANGE_RATE,
                    when=event.when,
                    original_amount=event.original_amount,
                    original_currency=event.original_currency,
                    description=event.description,
                )
            )
            unresolved.append(
                UnresolvedObligation(
                    reference_id=event.event_id,
                    source=event.provenance.kind.value,
                    direction=event.direction,
                    effective_date=event.when,
                    recurrence=event.source_cadence.value if event.source_cadence else None,
                    category=event.category,
                    description=event.description,
                    note="no supplied exchange rate for this date; never converted at 1:1",
                )
            )
            continue
        if not window.contains(event.when):
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=ExclusionReason.OUTSIDE_WINDOW,
                    when=event.when,
                    original_amount=event.original_amount,
                    original_currency=event.original_currency,
                    description=event.description,
                )
            )
            continue
        simulated_events.append(event)

    simulated_events.sort(key=ordering_key)
    daily_states = simulate(
        starting_state.available_cash, simulated_events, window, profile.minimum_balance_to_keep
    )

    cumulative_income = sum(
        (event.amount_home or _ZERO) for event in simulated_events if event.direction is Direction.CREDIT
    )
    cumulative_essential = sum(
        (event.amount_home or _ZERO)
        for event in simulated_events
        if event.direction is Direction.DEBIT and event.spending_class is not SpendingClass.FLEXIBLE
    )
    cumulative_flexible = sum(
        (event.amount_home or _ZERO)
        for event in simulated_events
        if event.direction is Direction.DEBIT and event.spending_class is SpendingClass.FLEXIBLE
    )

    minimum_state = min(daily_states, key=lambda state: (state.intraday_low_balance, state.day_index))
    deficit_dates = tuple(state.when for state in daily_states if state.breaches_minimum)

    forecast = FinancialStateForecast(
        request_id=bundle.request.request_id,
        user_id=profile.user_id,
        home_currency=profile.home_currency,
        window=window,
        starting_state=starting_state,
        events=tuple(simulated_events),
        excluded=tuple(excluded),
        unresolved_obligations=tuple(unresolved),
        daily_states=daily_states,
        minimum_projected_balance=minimum_state.intraday_low_balance,
        minimum_projected_balance_date=minimum_state.when,
        ending_balance=daily_states[-1].closing_balance,
        cumulative_future_income=Decimal(cumulative_income),
        cumulative_future_essential_expense=Decimal(cumulative_essential),
        cumulative_future_flexible_expense=Decimal(cumulative_flexible),
        safety_margin=minimum_state.intraday_low_balance - profile.minimum_balance_to_keep,
        breaches_minimum=bool(deficit_dates),
        deficit_dates=deficit_dates,
        has_unresolved_conversion=has_unresolved_conversion,
        notes=tuple(notes),
    )

    if verify_invariants:
        check_forecast_invariants(forecast)
    return forecast
