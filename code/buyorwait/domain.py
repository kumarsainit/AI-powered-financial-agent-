from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class RequestType(Enum):
    PURCHASE = "purchase"
    TRAVEL = "travel"
    EDUCATION = "education"
    FAMILY_TRANSFER = "family_transfer"
    DEBT_REPAYMENT = "debt_repayment"
    INVESTMENT = "investment"
    HOUSING = "housing"
    EMERGENCY_EXPENSE = "emergency_expense"
    OTHER = "other"


class PaymentMethod(Enum):
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"


class EventType(Enum):
    EXPENSE = "expense"
    SUBSCRIPTION = "subscription"
    INCOME = "income"
    DEBT_PAYMENT = "debt_payment"
    INVESTMENT_PURCHASE = "investment_purchase"
    REFUND = "refund"
    INVESTMENT_VALUATION = "investment_valuation"
    INVESTMENT_SALE = "investment_sale"


class Direction(Enum):
    DEBIT = "debit"
    CREDIT = "credit"
    NON_CASH = "non_cash"


class EventStatus(Enum):
    SETTLED = "settled"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNREALIZED = "unrealized"


class Flexibility(Enum):
    FIXED = "fixed"
    REDUCIBLE = "reducible"
    STOPPABLE = "stoppable"
    REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"


class Cadence(Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    OTHER = "other"
    NONE = "none"


class RecurrenceStrength(Enum):
    STRONG = "strong"
    WEAK = "weak"
    ONE_OFF = "one_off"


class LifecyclePattern(Enum):
    STANDALONE = "standalone"
    REFUND_SETTLED = "refund_settled"
    REFUND_PENDING = "refund_pending"
    CANCELLED_AUTHORIZATION = "cancelled_authorization"
    FAILED_RETRY = "failed_retry"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    UNREALIZED_VALUATION = "unrealized_valuation"
    REALIZED_SALE = "realized_sale"
    UNCLASSIFIED_LINK = "unclassified_link"


class MemberDisposition(Enum):
    INCLUDE = "include"
    EXCLUDE_SUPERSEDED = "exclude_superseded"
    EXCLUDE_PENDING_CREDIT = "exclude_pending_credit"
    EXCLUDE_NONCASH = "exclude_noncash"


@dataclass(frozen=True)
class UserFinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: tuple[str, ...]
    expense_categories_to_protect: tuple[str, ...]
    expense_categories_user_is_willing_to_reduce: tuple[str, ...]
    expense_categories_user_is_willing_to_stop: tuple[str, ...]
    payment_methods_user_will_consider: tuple[PaymentMethod, ...]
    max_installment_months: int | None


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: RequestType
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: EventType
    description: str
    category: str
    direction: Direction
    amount: Decimal | None
    currency: str
    event_date: date
    settlement_date: date | None
    status: EventStatus
    linked_event_id: str | None
    flexibility: Flexibility
    minimum_allowed_amount: Decimal | None


@dataclass(frozen=True)
class RecurringEventCandidate:
    user_id: str
    category: str
    event_type: EventType
    source_event_ids: tuple[str, ...]
    occurrence_count: int
    observed_dates: tuple[date, ...]
    observed_intervals_days: tuple[int, ...]
    amounts: tuple[Decimal, ...]
    currency: str
    inferred_cadence: Cadence
    strength: RecurrenceStrength
    reason: str


@dataclass(frozen=True)
class FutureProjectedEvent:
    user_id: str
    category: str
    projected_date: date
    projected_amount: Decimal | None
    currency: str
    basis: str
    source_pattern_id: str | None = None


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: PaymentMethod
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime
    source_type: str
    message_text: str


@dataclass(frozen=True)
class ImageEvidence:
    image_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    file_path: str


@dataclass(frozen=True)
class SpendingChangeCandidate:
    event_id: str
    user_id: str
    category: str
    flexibility: Flexibility
    current_amount: Decimal | None
    minimum_allowed_amount: Decimal | None
    user_permits_reduce: bool
    user_permits_stop: bool
    is_protected: bool
    recurrence_classification: RecurringEventCandidate | None


@dataclass(frozen=True)
class ExchangeRateRecord:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass(frozen=True)
class LifecycleMember:
    event_id: str
    role: str
    disposition: MemberDisposition


@dataclass(frozen=True)
class LifecycleChain:
    chain_id: str
    pattern: LifecyclePattern
    members: tuple[LifecycleMember, ...]

    def included_event_ids(self) -> tuple[str, ...]:
        return tuple(m.event_id for m in self.members if m.disposition is MemberDisposition.INCLUDE)

    def excluded_event_ids(self) -> tuple[str, ...]:
        return tuple(m.event_id for m in self.members if m.disposition is not MemberDisposition.INCLUDE)


@dataclass(frozen=True)
class HistoricalSpendingObservation:
    user_id: str
    category: str
    currency: str
    observations: tuple[tuple[date, Decimal], ...]
    historical_total: Decimal
    historical_average: Decimal
    historical_median: Decimal
    historical_maximum: Decimal
    recent_average: Decimal
    recent_maximum: Decimal
    occurrence_count: int
    spending_frequency_days: float | None
    observed_date_range: tuple[date, date]
