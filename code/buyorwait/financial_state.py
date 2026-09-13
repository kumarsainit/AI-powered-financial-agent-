from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from .domain import Cadence, Direction


class CashFlowKind(Enum):
    HISTORICAL_OBSERVED = "historical_observed"
    CONFIRMED_FUTURE = "confirmed_future"
    PROJECTED_RECURRING = "projected_recurring"
    VARIABLE_BASELINE = "variable_baseline"
    UNRESOLVED_OBLIGATION = "unresolved_obligation"


class Certainty(Enum):
    SETTLED = "settled"
    CONFIRMED = "confirmed"
    PROJECTED_STRONG = "projected_strong"
    PROJECTED_WEAK = "projected_weak"
    ESTIMATED = "estimated"
    SPECULATIVE = "speculative"
    UNRESOLVED = "unresolved"


class SpendingClass(Enum):
    ESSENTIAL = "essential"
    FLEXIBLE = "flexible"
    INCOME = "income"
    NOT_APPLICABLE = "not_applicable"


class ProvenanceKind(Enum):
    PROFILE_BALANCE = "profile_balance"
    LEDGER_EVENT = "ledger_event"
    RECURRENCE_PATTERN = "recurrence_pattern"
    EVIDENCE_FACT = "evidence_fact"
    VARIABLE_SPENDING_BASELINE = "variable_spending_baseline"


class ConversionStatus(Enum):
    SAME_CURRENCY = "same_currency"
    CONVERTED = "converted"
    UNRESOLVED_RATE = "unresolved_rate"
    NOT_APPLICABLE = "not_applicable"


class ExclusionReason(Enum):
    PENDING_CREDIT = "pending_credit"
    SPECULATIVE_INCOME = "speculative_income"
    UNREALIZED_INVESTMENT = "unrealized_investment"
    FAILED_TRANSACTION = "failed_transaction"
    CANCELLED_TRANSACTION = "cancelled_transaction"
    SUPERSEDED_DUPLICATE = "superseded_duplicate"
    MISSING_AMOUNT = "missing_amount"
    MISSING_EXCHANGE_RATE = "missing_exchange_rate"
    OUTSIDE_WINDOW = "outside_window"
    IRRELEVANT_EVIDENCE = "irrelevant_evidence"
    UNRESOLVED_EVIDENCE = "unresolved_evidence"


@dataclass(frozen=True)
class Provenance:
    kind: ProvenanceKind
    reference_id: str
    detail: str


@dataclass(frozen=True)
class ForecastWindow:
    start_date: date
    horizon_days: int

    @property
    def end_date(self) -> date:
        from datetime import timedelta

        return self.start_date + timedelta(days=self.horizon_days)

    def contains(self, when: date) -> bool:
        return self.start_date <= when <= self.end_date


@dataclass(frozen=True)
class ProjectedCashFlowEvent:
    event_id: str
    kind: CashFlowKind
    certainty: Certainty
    spending_class: SpendingClass
    when: date
    direction: Direction
    original_amount: Decimal | None
    original_currency: str
    amount_home: Decimal | None
    home_currency: str
    conversion_status: ConversionStatus
    category: str
    description: str
    provenance: Provenance
    source_cadence: Cadence | None
    source_event_ids: tuple[str, ...]
    is_reducible: bool
    is_stoppable: bool
    is_protected: bool

    @property
    def signed_amount_home(self) -> Decimal | None:
        if self.amount_home is None:
            return None
        if self.direction is Direction.CREDIT:
            return self.amount_home
        if self.direction is Direction.DEBIT:
            return -self.amount_home
        return Decimal("0")


@dataclass(frozen=True)
class ExcludedCashFlow:
    reference_id: str
    reason: ExclusionReason
    when: date | None
    original_amount: Decimal | None
    original_currency: str | None
    description: str


@dataclass(frozen=True)
class UnresolvedObligation:
    reference_id: str
    source: str
    direction: Direction
    effective_date: date | None
    recurrence: str | None
    category: str | None
    description: str
    note: str


@dataclass(frozen=True)
class ReservedItem:
    event_id: str
    amount_home: Decimal | None
    direction: Direction
    when: date
    reason: str
    conversion_status: ConversionStatus


@dataclass(frozen=True)
class StartingFinancialState:
    user_id: str
    request_id: str
    as_of: date
    home_currency: str
    reported_balance: Decimal
    reserved_total: Decimal
    available_cash: Decimal
    minimum_balance_to_keep: Decimal
    reserved_items: tuple[ReservedItem, ...]
    excluded_items: tuple[ExcludedCashFlow, ...]
    unrealized_investment_value: Decimal
    ledger_event_count: int
    ledger_included_event_count: int
    last_ledger_event_date: date | None


@dataclass(frozen=True)
class DailyFinancialState:
    day_index: int
    when: date
    opening_balance: Decimal
    income_applied: Decimal
    essential_expense_applied: Decimal
    flexible_expense_applied: Decimal
    closing_balance: Decimal
    intraday_low_balance: Decimal
    margin_to_minimum: Decimal
    breaches_minimum: bool
    applied_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class FinancialStateForecast:
    request_id: str
    user_id: str
    home_currency: str
    window: ForecastWindow
    starting_state: StartingFinancialState
    events: tuple[ProjectedCashFlowEvent, ...]
    excluded: tuple[ExcludedCashFlow, ...]
    unresolved_obligations: tuple[UnresolvedObligation, ...]
    daily_states: tuple[DailyFinancialState, ...]
    minimum_projected_balance: Decimal
    minimum_projected_balance_date: date
    ending_balance: Decimal
    cumulative_future_income: Decimal
    cumulative_future_essential_expense: Decimal
    cumulative_future_flexible_expense: Decimal
    safety_margin: Decimal
    breaches_minimum: bool
    deficit_dates: tuple[date, ...]
    has_unresolved_conversion: bool
    notes: tuple[str, ...]
