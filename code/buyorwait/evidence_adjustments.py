from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal

from .currency import ExchangeRateTable
from .domain import Direction, UserFinancialProfile
from .errors import ExchangeRateNotFoundError
from .evidence import EvidenceBundle, EvidenceFact, EvidenceStatus, FactType
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
    SpendingClass,
    UnresolvedObligation,
)
from .projection import add_months

_INCOME_STOP_TYPES = (FactType.INCOME_TERMINATED, FactType.INCOME_SUSPENDED)
_INCOME_AMEND_TYPES = (FactType.SALARY_RAISE, FactType.SALARY_CUT)
_SPECULATIVE_CREDIT_TYPES = (
    FactType.INCOME_BONUS_PENDING,
    FactType.PRIZE_CLAIM_PENDING,
    FactType.PRIZE_CLAIM_UNVERIFIED,
    FactType.REFUND_PENDING,
    FactType.REFUND_PROCESSING,
    FactType.FOREIGN_CURRENCY_REFUND_PROCESSING,
    FactType.INCOME_INVOICE_APPROVED,
)
_NON_CASH_TYPES = (FactType.INVESTMENT_UNREALIZED_GAIN, FactType.INVESTMENT_UNREALIZED_LOSS)
_NOTE_ONLY_TYPES = (
    FactType.EVENT_SETTLEMENT_CONFIRMATION,
    FactType.REFUND_SETTLED,
    FactType.TRANSFER_INTERNAL,
)
_INCOME_CATEGORY = "salary"
_MAX_RECURRING_OCCURRENCES = 12


@dataclass(frozen=True)
class EvidenceAdjustmentResult:
    events: tuple[ProjectedCashFlowEvent, ...]
    excluded: tuple[ExcludedCashFlow, ...]
    unresolved: tuple[UnresolvedObligation, ...]
    notes: tuple[str, ...]


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


def _unresolved(fact: EvidenceFact, direction: Direction, note: str) -> UnresolvedObligation:
    return UnresolvedObligation(
        reference_id=fact.fact_id,
        source=fact.source_type.value,
        direction=direction,
        effective_date=fact.effective_date,
        recurrence=fact.recurrence,
        category=fact.category,
        description=fact.description,
        note=note,
    )


def _excluded(fact: EvidenceFact, reason: ExclusionReason) -> ExcludedCashFlow:
    return ExcludedCashFlow(
        reference_id=fact.fact_id,
        reason=reason,
        when=fact.effective_date,
        original_amount=fact.amount,
        original_currency=fact.currency,
        description=fact.description,
    )


def _recurrence_dates(start: date, recurrence: str | None, window: ForecastWindow) -> tuple[date, ...]:
    if start > window.end_date:
        return ()
    anchor = max(start, window.start_date)
    dates: list[date] = []
    normalized = (recurrence or "").lower()
    if "week" in normalized:
        step_days = 7
        current = anchor
        while current <= window.end_date and len(dates) < _MAX_RECURRING_OCCURRENCES * 6:
            dates.append(current)
            current = date.fromordinal(current.toordinal() + step_days)
        return tuple(dates)
    if not normalized or "month" in normalized:
        index = 0
        while index < _MAX_RECURRING_OCCURRENCES:
            occurrence = add_months(start, index, start.day)
            if occurrence > window.end_date:
                break
            if occurrence >= window.start_date:
                dates.append(occurrence)
            index += 1
        return tuple(dates)
    return (anchor,)


def _build_event(
    fact: EvidenceFact,
    when: date,
    direction: Direction,
    amount: Decimal,
    profile: UserFinancialProfile,
    rates: ExchangeRateTable,
    index: int,
    certainty: Certainty,
) -> tuple[ProjectedCashFlowEvent | None, ExcludedCashFlow | None]:
    currency = fact.currency or profile.home_currency
    amount_home, status = _convert(amount, when, currency, profile.home_currency, rates)
    if amount_home is None:
        return None, _excluded(fact, ExclusionReason.MISSING_EXCHANGE_RATE)
    category = fact.category or (_INCOME_CATEGORY if direction is Direction.CREDIT else "other")
    spending_class = SpendingClass.INCOME if direction is Direction.CREDIT else SpendingClass.ESSENTIAL
    return (
        ProjectedCashFlowEvent(
            event_id=f"evidence:{fact.fact_id}:{index}",
            kind=CashFlowKind.CONFIRMED_FUTURE,
            certainty=certainty,
            spending_class=spending_class,
            when=when,
            direction=direction,
            original_amount=amount,
            original_currency=currency,
            amount_home=amount_home,
            home_currency=profile.home_currency,
            conversion_status=status,
            category=category,
            description=fact.description,
            provenance=Provenance(
                kind=ProvenanceKind.EVIDENCE_FACT,
                reference_id=fact.fact_id,
                detail=f"{fact.fact_type.value} from {fact.source_type.value} {fact.source_id}",
            ),
            source_cadence=None,
            source_event_ids=(fact.event_id,) if fact.event_id else (),
            is_reducible=False,
            is_stoppable=False,
            is_protected=category in profile.expense_categories_to_protect,
        ),
        None,
    )


def _sort_timestamp(fact: EvidenceFact, window: ForecastWindow) -> datetime:
    if fact.created_at is not None:
        stamp = fact.created_at
        return stamp.replace(tzinfo=None) if stamp.tzinfo is not None else stamp
    anchor = fact.effective_date or window.start_date
    return datetime.combine(anchor, time.min)


def _is_income_event(event: ProjectedCashFlowEvent) -> bool:
    return event.direction is Direction.CREDIT


def apply_evidence(
    evidence: EvidenceBundle,
    projected: tuple[ProjectedCashFlowEvent, ...],
    profile: UserFinancialProfile,
    window: ForecastWindow,
    rates: ExchangeRateTable,
) -> EvidenceAdjustmentResult:
    events = list(projected)
    excluded: list[ExcludedCashFlow] = []
    unresolved: list[UnresolvedObligation] = []
    notes: list[str] = []

    for fact in sorted(evidence.facts, key=lambda f: (_sort_timestamp(f, window), f.fact_id)):
        if not fact.is_trusted or fact.fact_type is FactType.IRRELEVANT:
            excluded.append(_excluded(fact, ExclusionReason.IRRELEVANT_EVIDENCE))
            continue

        if fact.fact_type in _NON_CASH_TYPES:
            excluded.append(_excluded(fact, ExclusionReason.UNREALIZED_INVESTMENT))
            continue

        if fact.fact_type in _SPECULATIVE_CREDIT_TYPES and fact.status is not EvidenceStatus.CONFIRMED:
            excluded.append(_excluded(fact, ExclusionReason.SPECULATIVE_INCOME))
            continue

        if fact.fact_type in _SPECULATIVE_CREDIT_TYPES:
            excluded.append(_excluded(fact, ExclusionReason.SPECULATIVE_INCOME))
            notes.append(f"{fact.fact_id}: pending credit not counted until settlement")
            continue

        if fact.fact_type in _NOTE_ONLY_TYPES:
            notes.append(f"{fact.fact_id}: {fact.fact_type.value} recorded, no future cash effect")
            continue

        if fact.status is EvidenceStatus.UNRESOLVED or fact.fact_type is FactType.UNRESOLVED:
            direction = Direction.CREDIT if fact.fact_type.value.startswith("income") else Direction.DEBIT
            unresolved.append(_unresolved(fact, direction, "evidence fact unresolved; amount never assumed"))
            excluded.append(_excluded(fact, ExclusionReason.UNRESOLVED_EVIDENCE))
            continue

        if fact.fact_type is FactType.CHARGE_DISPUTED:
            unresolved.append(
                _unresolved(fact, Direction.DEBIT, "disputed charge: no cash relief assumed until resolved")
            )
            continue

        if fact.fact_type in _INCOME_STOP_TYPES:
            cutoff = fact.effective_date or window.start_date
            category = fact.category or _INCOME_CATEGORY
            kept = []
            for event in events:
                if _is_income_event(event) and event.category == category and event.when >= cutoff:
                    excluded.append(
                        ExcludedCashFlow(
                            reference_id=event.event_id,
                            reason=ExclusionReason.SPECULATIVE_INCOME,
                            when=event.when,
                            original_amount=event.original_amount,
                            original_currency=event.original_currency,
                            description=f"income terminated by {fact.fact_id}",
                        )
                    )
                    continue
                kept.append(event)
            events = kept
            notes.append(f"{fact.fact_id}: income projection for '{category}' stopped from {cutoff.isoformat()}")
            continue

        if fact.fact_type is FactType.EXPENSE_TERMINATED:
            cutoff = fact.effective_date or window.start_date
            category = fact.category
            if category is None:
                unresolved.append(_unresolved(fact, Direction.DEBIT, "expense termination without a category"))
                continue
            events = [
                event
                for event in events
                if not (event.direction is Direction.DEBIT and event.category == category and event.when >= cutoff)
            ]
            notes.append(f"{fact.fact_id}: expense projection for '{category}' stopped from {cutoff.isoformat()}")
            continue

        if fact.fact_type is FactType.EVENT_CANCELLATION:
            if fact.event_id is None:
                unresolved.append(_unresolved(fact, Direction.DEBIT, "cancellation without a linked event"))
                continue
            events = [event for event in events if fact.event_id not in event.source_event_ids]
            notes.append(f"{fact.fact_id}: cancelled future occurrences linked to {fact.event_id}")
            continue

        if fact.fact_type in _INCOME_AMEND_TYPES:
            if fact.amount is None:
                unresolved.append(_unresolved(fact, Direction.CREDIT, "income change without a stated amount"))
                continue
            effective = fact.effective_date or window.start_date
            category = fact.category or _INCOME_CATEGORY
            amended = []
            for event in events:
                if _is_income_event(event) and event.category == category and event.when >= effective:
                    amount_home, status = _convert(
                        fact.amount, event.when, fact.currency or profile.home_currency, profile.home_currency, rates
                    )
                    amended.append(
                        replace(
                            event,
                            original_amount=fact.amount,
                            original_currency=fact.currency or profile.home_currency,
                            amount_home=amount_home,
                            conversion_status=status,
                            certainty=Certainty.CONFIRMED,
                            provenance=Provenance(
                                kind=ProvenanceKind.EVIDENCE_FACT,
                                reference_id=fact.fact_id,
                                detail=f"{fact.fact_type.value} amends projected income from {effective.isoformat()}",
                            ),
                        )
                        if amount_home is not None
                        else event
                    )
                    if amount_home is None:
                        excluded.append(_excluded(fact, ExclusionReason.MISSING_EXCHANGE_RATE))
                    continue
                amended.append(event)
            events = amended
            notes.append(
                f"{fact.fact_id}: income for '{category}' amended to {fact.amount} from {effective.isoformat()}"
            )
            continue

        if fact.fact_type is FactType.EXPENSE_RENT_INCREASE:
            if fact.amount is None:
                unresolved.append(_unresolved(fact, Direction.DEBIT, "rent increase without a stated amount"))
                continue
            effective = fact.effective_date or window.start_date
            category = fact.category or "rent"
            amended = []
            for event in events:
                if event.direction is Direction.DEBIT and event.category == category and event.when >= effective:
                    amount_home, status = _convert(
                        fact.amount, event.when, fact.currency or profile.home_currency, profile.home_currency, rates
                    )
                    if amount_home is None:
                        excluded.append(_excluded(fact, ExclusionReason.MISSING_EXCHANGE_RATE))
                        amended.append(event)
                        continue
                    amended.append(
                        replace(
                            event,
                            original_amount=fact.amount,
                            original_currency=fact.currency or profile.home_currency,
                            amount_home=amount_home,
                            conversion_status=status,
                            certainty=Certainty.CONFIRMED,
                            provenance=Provenance(
                                kind=ProvenanceKind.EVIDENCE_FACT,
                                reference_id=fact.fact_id,
                                detail=f"rent increase effective {effective.isoformat()}",
                            ),
                        )
                    )
                    continue
                amended.append(event)
            events = amended
            notes.append(f"{fact.fact_id}: rent amended to {fact.amount} from {effective.isoformat()}")
            continue

        if fact.fact_type is FactType.SALARY_DATE_SHIFT:
            if fact.effective_date is None:
                unresolved.append(_unresolved(fact, Direction.CREDIT, "salary date shift without a date"))
                continue
            category = fact.category or _INCOME_CATEGORY
            future_income = [
                event for event in events if _is_income_event(event) and event.category == category
            ]
            if not future_income:
                notes.append(f"{fact.fact_id}: salary date shift with no projected salary to move")
                continue
            target = min(future_income, key=lambda e: e.when)
            events = [
                replace(
                    event,
                    when=fact.effective_date,
                    certainty=Certainty.CONFIRMED,
                    provenance=Provenance(
                        kind=ProvenanceKind.EVIDENCE_FACT,
                        reference_id=fact.fact_id,
                        detail="salary payment date shifted by evidence",
                    ),
                )
                if event.event_id == target.event_id
                else event
                for event in events
            ]
            notes.append(f"{fact.fact_id}: salary payment moved to {fact.effective_date.isoformat()}")
            continue

        if fact.fact_type in (FactType.SALARY_FIRST, FactType.SALARY_CONFIRMED, FactType.INCOME_RESUMED):
            if fact.amount is None or fact.effective_date is None:
                unresolved.append(
                    _unresolved(fact, Direction.CREDIT, "confirmed income without both an amount and a date")
                )
                continue
            occurrences = _recurrence_dates(fact.effective_date, fact.recurrence, window)
            if not occurrences:
                continue
            for index, when in enumerate(occurrences, start=1):
                event, exclusion = _build_event(
                    fact, when, Direction.CREDIT, fact.amount, profile, rates, index, Certainty.CONFIRMED
                )
                if event is not None:
                    events.append(event)
                if exclusion is not None:
                    excluded.append(exclusion)
            continue

        if fact.fact_type in (
            FactType.EXPENSE_NEW_RECURRING,
            FactType.EXPENSE_AMOUNT,
            FactType.DEBIT_FAILED_RETRY,
            FactType.EVENT_AMENDMENT,
            FactType.EVENT_DELAY,
        ):
            if fact.amount is None:
                unresolved.append(
                    _unresolved(fact, Direction.DEBIT, "future obligation confirmed but amount not stated")
                )
                continue
            if fact.effective_date is None:
                unresolved.append(
                    _unresolved(fact, Direction.DEBIT, "future obligation confirmed but date not stated")
                )
                continue
            recurrence = fact.recurrence if fact.fact_type is FactType.EXPENSE_NEW_RECURRING else "once"
            occurrences = _recurrence_dates(fact.effective_date, recurrence, window)
            if fact.fact_type is not FactType.EXPENSE_NEW_RECURRING:
                occurrences = tuple(d for d in occurrences if window.contains(d))
            for index, when in enumerate(occurrences, start=1):
                event, exclusion = _build_event(
                    fact, when, Direction.DEBIT, fact.amount, profile, rates, index, Certainty.CONFIRMED
                )
                if event is not None:
                    events.append(event)
                if exclusion is not None:
                    excluded.append(exclusion)
            continue

        unresolved.append(
            _unresolved(
                fact,
                Direction.CREDIT if fact.fact_type.value.startswith("income") else Direction.DEBIT,
                f"no financial-state handling defined for {fact.fact_type.value}",
            )
        )

    return EvidenceAdjustmentResult(
        events=tuple(events),
        excluded=tuple(excluded),
        unresolved=tuple(unresolved),
        notes=tuple(notes),
    )
