from __future__ import annotations

from decimal import Decimal

from .financial_state import (
    CashFlowKind,
    Certainty,
    ConversionStatus,
    FinancialStateForecast,
)


class InvariantViolation(AssertionError):
    pass


def check_forecast_invariants(forecast: FinancialStateForecast) -> None:
    window = forecast.window

    seen_ids: set[str] = set()
    for event in forecast.events:
        if event.event_id in seen_ids:
            raise InvariantViolation(f"{forecast.request_id}: duplicate projected event id {event.event_id}")
        seen_ids.add(event.event_id)

        if event.when < window.start_date:
            raise InvariantViolation(
                f"{forecast.request_id}: projected event {event.event_id} dated before request_date"
            )
        if event.when > window.end_date:
            raise InvariantViolation(
                f"{forecast.request_id}: projected event {event.event_id} dated beyond the forecast horizon"
            )
        if event.provenance is None or not event.provenance.reference_id:
            raise InvariantViolation(f"{forecast.request_id}: projected event {event.event_id} has no provenance")
        if event.certainty is Certainty.SPECULATIVE:
            raise InvariantViolation(
                f"{forecast.request_id}: speculative event {event.event_id} entered the cash simulation"
            )
        if event.kind is CashFlowKind.UNRESOLVED_OBLIGATION:
            raise InvariantViolation(
                f"{forecast.request_id}: unresolved obligation {event.event_id} entered the cash simulation"
            )
        if event.amount_home is None and event.conversion_status is not ConversionStatus.UNRESOLVED_RATE:
            raise InvariantViolation(
                f"{forecast.request_id}: event {event.event_id} has no home amount and no unresolved-rate marker"
            )
        if event.original_amount is None:
            raise InvariantViolation(
                f"{forecast.request_id}: event {event.event_id} entered the simulation without an amount"
            )

    if len(forecast.daily_states) != window.horizon_days + 1:
        raise InvariantViolation(
            f"{forecast.request_id}: daily states do not cover the whole forecast window"
        )
    if forecast.daily_states[0].when != window.start_date:
        raise InvariantViolation(f"{forecast.request_id}: first daily state is not request_date")
    if forecast.daily_states[-1].when != window.end_date:
        raise InvariantViolation(f"{forecast.request_id}: last daily state is not the horizon end")

    computed_minimum = min(state.intraday_low_balance for state in forecast.daily_states)
    if computed_minimum != forecast.minimum_projected_balance:
        raise InvariantViolation(f"{forecast.request_id}: minimum projected balance is inconsistent")

    for obligation in forecast.unresolved_obligations:
        if obligation.reference_id in seen_ids:
            raise InvariantViolation(
                f"{forecast.request_id}: unresolved obligation {obligation.reference_id} also became a cash event"
            )

    starting = forecast.starting_state
    expected_available = starting.reported_balance - starting.reserved_total
    if starting.available_cash != expected_available:
        raise InvariantViolation(f"{forecast.request_id}: starting available cash does not match reservations")
    if starting.as_of != window.start_date:
        raise InvariantViolation(f"{forecast.request_id}: starting state is not as of request_date")
    if not isinstance(starting.available_cash, Decimal):
        raise InvariantViolation(f"{forecast.request_id}: monetary values must be Decimal")
