from __future__ import annotations

from datetime import date
from decimal import Decimal

from .bundle import RequestBundle
from .currency import ExchangeRateTable
from .domain import (
    Direction,
    EventStatus,
    EventType,
    FinancialEvent,
    Flexibility,
    MemberDisposition,
    UserFinancialProfile,
)
from .errors import ExchangeRateNotFoundError
from .financial_state import (
    CashFlowKind,
    Certainty,
    ConversionStatus,
    ExcludedCashFlow,
    ExclusionReason,
    ForecastWindow,
    ProjectedCashFlowEvent,
    Provenance,
    ProvenanceKind,
    ReservedItem,
    SpendingClass,
    StartingFinancialState,
    UnresolvedObligation,
)

_DISPOSITION_REASONS = {
    MemberDisposition.EXCLUDE_SUPERSEDED: ExclusionReason.SUPERSEDED_DUPLICATE,
    MemberDisposition.EXCLUDE_PENDING_CREDIT: ExclusionReason.PENDING_CREDIT,
    MemberDisposition.EXCLUDE_NONCASH: ExclusionReason.UNREALIZED_INVESTMENT,
}

_NON_CASH_TYPES = (EventType.INVESTMENT_VALUATION,)


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


def _cash_date(event: FinancialEvent) -> date:
    return event.settlement_date or event.event_date


def build_starting_state(
    bundle: RequestBundle,
    rates: ExchangeRateTable,
) -> tuple[StartingFinancialState, tuple[UnresolvedObligation, ...]]:
    profile: UserFinancialProfile = bundle.profile
    request_date = bundle.request.request_date
    home = profile.home_currency

    reserved: list[ReservedItem] = []
    excluded: list[ExcludedCashFlow] = []
    unresolved: list[UnresolvedObligation] = []
    unrealized_value = Decimal("0")

    disposition_by_event: dict[str, MemberDisposition] = {}
    for chain in bundle.lifecycle_chains:
        for member in chain.members:
            disposition_by_event[member.event_id] = member.disposition

    for event in sorted(bundle.events, key=lambda e: (e.event_date, e.event_id)):
        disposition = disposition_by_event.get(event.event_id, MemberDisposition.INCLUDE)
        if disposition is not MemberDisposition.INCLUDE:
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=_DISPOSITION_REASONS[disposition],
                    when=event.event_date,
                    original_amount=event.amount,
                    original_currency=event.currency,
                    description=event.description,
                )
            )
            if disposition is MemberDisposition.EXCLUDE_NONCASH and event.amount is not None:
                converted, _status = _convert(event.amount, _cash_date(event), event.currency, home, rates)
                if converted is not None:
                    unrealized_value += converted
            continue

        if event.status is EventStatus.FAILED:
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=ExclusionReason.FAILED_TRANSACTION,
                    when=event.event_date,
                    original_amount=event.amount,
                    original_currency=event.currency,
                    description=event.description,
                )
            )
            continue

        if event.status is EventStatus.CANCELLED:
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=ExclusionReason.CANCELLED_TRANSACTION,
                    when=event.event_date,
                    original_amount=event.amount,
                    original_currency=event.currency,
                    description=event.description,
                )
            )
            continue

        if (
            event.status is EventStatus.UNREALIZED
            or event.direction is Direction.NON_CASH
            or event.event_type in _NON_CASH_TYPES
        ):
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=ExclusionReason.UNREALIZED_INVESTMENT,
                    when=event.event_date,
                    original_amount=event.amount,
                    original_currency=event.currency,
                    description=event.description,
                )
            )
            if event.amount is not None:
                converted, status = _convert(event.amount, _cash_date(event), event.currency, home, rates)
                if converted is not None:
                    unrealized_value += converted
            continue

        if event.status is EventStatus.SETTLED:
            continue

        if event.amount is None:
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=ExclusionReason.MISSING_AMOUNT,
                    when=event.event_date,
                    original_amount=None,
                    original_currency=event.currency,
                    description=event.description,
                )
            )
            unresolved.append(
                UnresolvedObligation(
                    reference_id=event.event_id,
                    source="financial_events",
                    direction=event.direction,
                    effective_date=_cash_date(event),
                    recurrence=None,
                    category=event.category,
                    description=event.description,
                    note="ledger row has no amount; never defaulted to zero",
                )
            )
            continue

        if event.status is EventStatus.PENDING:
            if event.direction is Direction.CREDIT:
                excluded.append(
                    ExcludedCashFlow(
                        reference_id=event.event_id,
                        reason=ExclusionReason.PENDING_CREDIT,
                        when=event.event_date,
                        original_amount=event.amount,
                        original_currency=event.currency,
                        description=event.description,
                    )
                )
                continue
            converted, status = _convert(event.amount, _cash_date(event), event.currency, home, rates)
            if converted is None:
                excluded.append(
                    ExcludedCashFlow(
                        reference_id=event.event_id,
                        reason=ExclusionReason.MISSING_EXCHANGE_RATE,
                        when=event.event_date,
                        original_amount=event.amount,
                        original_currency=event.currency,
                        description=event.description,
                    )
                )
                unresolved.append(
                    UnresolvedObligation(
                        reference_id=event.event_id,
                        source="financial_events",
                        direction=event.direction,
                        effective_date=_cash_date(event),
                        recurrence=None,
                        category=event.category,
                        description=event.description,
                        note="pending debit in a currency with no supplied exchange rate",
                    )
                )
                continue
            reserved.append(
                ReservedItem(
                    event_id=event.event_id,
                    amount_home=converted,
                    direction=event.direction,
                    when=_cash_date(event),
                    reason="pending debit reserved against available cash",
                    conversion_status=status,
                )
            )
            continue

        if event.status is EventStatus.SCHEDULED and event.event_date < request_date:
            if event.direction is Direction.CREDIT:
                excluded.append(
                    ExcludedCashFlow(
                        reference_id=event.event_id,
                        reason=ExclusionReason.SPECULATIVE_INCOME,
                        when=event.event_date,
                        original_amount=event.amount,
                        original_currency=event.currency,
                        description=event.description,
                    )
                )
                continue
            converted, status = _convert(event.amount, _cash_date(event), event.currency, home, rates)
            if converted is None:
                excluded.append(
                    ExcludedCashFlow(
                        reference_id=event.event_id,
                        reason=ExclusionReason.MISSING_EXCHANGE_RATE,
                        when=event.event_date,
                        original_amount=event.amount,
                        original_currency=event.currency,
                        description=event.description,
                    )
                )
                continue
            reserved.append(
                ReservedItem(
                    event_id=event.event_id,
                    amount_home=converted,
                    direction=event.direction,
                    when=_cash_date(event),
                    reason="scheduled debit dated before request_date reserved against available cash",
                    conversion_status=status,
                )
            )

    reserved_total = sum((item.amount_home or Decimal("0")) for item in reserved)
    included_ids = bundle.included_event_ids
    last_event_date = max((e.event_date for e in bundle.events), default=None)

    state = StartingFinancialState(
        user_id=profile.user_id,
        request_id=bundle.request.request_id,
        as_of=request_date,
        home_currency=home,
        reported_balance=profile.current_available_balance,
        reserved_total=reserved_total,
        available_cash=profile.current_available_balance - reserved_total,
        minimum_balance_to_keep=profile.minimum_balance_to_keep,
        reserved_items=tuple(reserved),
        excluded_items=tuple(excluded),
        unrealized_investment_value=unrealized_value,
        ledger_event_count=len(bundle.events),
        ledger_included_event_count=len(included_ids),
        last_ledger_event_date=last_event_date,
    )
    return state, tuple(unresolved)


def build_confirmed_future_events(
    bundle: RequestBundle,
    window: ForecastWindow,
    rates: ExchangeRateTable,
) -> tuple[tuple[ProjectedCashFlowEvent, ...], tuple[ExcludedCashFlow, ...], tuple[UnresolvedObligation, ...]]:
    profile = bundle.profile
    home = profile.home_currency
    events: list[ProjectedCashFlowEvent] = []
    excluded: list[ExcludedCashFlow] = []
    unresolved: list[UnresolvedObligation] = []

    for event in sorted(bundle.events, key=lambda e: (e.event_date, e.event_id)):
        if event.event_id not in bundle.included_event_ids:
            continue
        if event.status is not EventStatus.SCHEDULED:
            continue
        when = _cash_date(event)
        if when < window.start_date:
            continue
        if not window.contains(when):
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=ExclusionReason.OUTSIDE_WINDOW,
                    when=when,
                    original_amount=event.amount,
                    original_currency=event.currency,
                    description=event.description,
                )
            )
            continue
        if event.amount is None:
            unresolved.append(
                UnresolvedObligation(
                    reference_id=event.event_id,
                    source="financial_events",
                    direction=event.direction,
                    effective_date=when,
                    recurrence=None,
                    category=event.category,
                    description=event.description,
                    note="scheduled future row has no amount",
                )
            )
            continue
        amount_home, status = _convert(event.amount, when, event.currency, home, rates)
        if amount_home is None:
            excluded.append(
                ExcludedCashFlow(
                    reference_id=event.event_id,
                    reason=ExclusionReason.MISSING_EXCHANGE_RATE,
                    when=when,
                    original_amount=event.amount,
                    original_currency=event.currency,
                    description=event.description,
                )
            )
            unresolved.append(
                UnresolvedObligation(
                    reference_id=event.event_id,
                    source="financial_events",
                    direction=event.direction,
                    effective_date=when,
                    recurrence=None,
                    category=event.category,
                    description=event.description,
                    note="scheduled future row in a currency with no supplied exchange rate",
                )
            )
            continue

        spending_class = (
            SpendingClass.INCOME
            if event.direction is Direction.CREDIT
            else (SpendingClass.ESSENTIAL if event.flexibility is Flexibility.FIXED else SpendingClass.FLEXIBLE)
        )
        events.append(
            ProjectedCashFlowEvent(
                event_id=f"confirmed:{event.event_id}",
                kind=CashFlowKind.CONFIRMED_FUTURE,
                certainty=Certainty.CONFIRMED,
                spending_class=spending_class,
                when=when,
                direction=event.direction,
                original_amount=event.amount,
                original_currency=event.currency,
                amount_home=amount_home,
                home_currency=home,
                conversion_status=status,
                category=event.category,
                description=event.description,
                provenance=Provenance(
                    kind=ProvenanceKind.LEDGER_EVENT,
                    reference_id=event.event_id,
                    detail="scheduled ledger row inside the forecast window",
                ),
                source_cadence=None,
                source_event_ids=(event.event_id,),
                is_reducible=(
                    event.category in profile.expense_categories_user_is_willing_to_reduce
                    and event.flexibility in (Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE)
                ),
                is_stoppable=(
                    event.category in profile.expense_categories_user_is_willing_to_stop
                    and event.flexibility in (Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE)
                ),
                is_protected=event.category in profile.expense_categories_to_protect,
            )
        )

    return tuple(events), tuple(excluded), tuple(unresolved)
