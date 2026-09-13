from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from .domain import PaymentMethod
from .financial_state import ConversionStatus, UnresolvedObligation


class AffordabilityStatus(Enum):
    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class RecommendedMethod(Enum):
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


class CandidateKind(Enum):
    FULL_PAYMENT_TODAY = "full_payment_today"
    FULL_PAYMENT_LATER = "full_payment_later"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    NO_PURCHASE = "no_purchase"


class RejectionReason(Enum):
    INSUFFICIENT_CURRENT_SURPLUS = "insufficient_current_surplus"
    FUTURE_CASH_FLOW_DEFICIT = "future_cash_flow_deficit"
    MINIMUM_BALANCE_VIOLATION = "minimum_balance_violation"
    PAYMENT_METHOD_INELIGIBLE = "payment_method_ineligible"
    PAYMENT_METHOD_NOT_PREFERRED = "payment_method_not_preferred"
    INSTALLMENT_TERM_TOO_LONG = "installment_term_too_long"
    INSTALLMENT_UNSAFE = "installment_unsafe"
    PARTIAL_PAYMENT_NOT_ALLOWED = "partial_payment_not_allowed"
    PARTIAL_PAYMENT_AMOUNT_INVALID = "partial_payment_amount_invalid"
    COMPLETION_AFTER_DEADLINE = "completion_after_deadline"
    NO_SAFE_DATE_IN_HORIZON = "no_safe_date_in_horizon"
    REQUIRED_SPENDING_REDUCTION_UNAVAILABLE = "required_spending_reduction_unavailable"
    BLOCKING_UNRESOLVED_OBLIGATION = "blocking_unresolved_obligation"
    UNRESOLVED_CURRENCY_CONVERSION = "unresolved_currency_conversion"
    PAYMENT_OUTSIDE_FORECAST_WINDOW = "payment_outside_forecast_window"


class ChangeAction(Enum):
    STOP = "stop"
    REDUCE_TO = "reduce_to"


@dataclass(frozen=True)
class SpendingChange:
    action: ChangeAction
    event_id: str
    category: str
    description: str
    current_amount: Decimal
    new_amount: Decimal
    minimum_allowed_amount: Decimal | None
    monthly_saving: Decimal
    source_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class PurchaseSpec:
    request_id: str
    user_id: str
    request_date: date
    desired_completion_date: date
    requested_amount_original: Decimal
    requested_currency: str
    requested_amount_home: Decimal | None
    home_currency: str
    conversion_status: ConversionStatus
    allows_partial_payment: bool
    accepted_methods: tuple[PaymentMethod, ...]
    max_installment_months: int | None
    minimum_balance_to_keep: Decimal
    available_cash: Decimal
    request_type: str


@dataclass(frozen=True)
class PaymentOptionModel:
    payment_option_id: str
    payment_method: PaymentMethod
    upfront_amount: Decimal
    payments: tuple[tuple[date, Decimal], ...]
    number_of_payments: int
    total_payable_amount: Decimal
    financing_fee: Decimal
    first_payment_date: date
    last_payment_date: date
    payment_frequency_days: int | None
    is_eligible: bool
    eligibility_reason: RejectionReason | None
    is_preferred: bool
    provenance: str


@dataclass(frozen=True)
class GateResult:
    financial_capacity: bool
    method_eligibility: bool
    user_preference: bool

    @property
    def passes(self) -> bool:
        return self.financial_capacity and self.method_eligibility and self.user_preference


@dataclass(frozen=True)
class CandidatePlan:
    candidate_id: str
    kind: CandidateKind
    method: RecommendedMethod
    payment_option_id: str | None
    payments: tuple[tuple[date, Decimal], ...]
    total_paid: Decimal
    financing_fee: Decimal
    spending_changes: tuple[SpendingChange, ...]
    completes_request: bool
    completion_date: date | None
    provenance: str


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: CandidatePlan
    gates: GateResult
    minimum_projected_balance: Decimal
    minimum_projected_balance_date: date
    safety_margin: Decimal
    breaches_minimum: bool
    payments_inside_window: int
    payments_outside_window: int
    rejection_reasons: tuple[RejectionReason, ...]

    @property
    def is_selectable(self) -> bool:
        return self.gates.passes and not self.rejection_reasons


@dataclass(frozen=True)
class ExplanationFacts:
    available_cash_at_request_date: Decimal
    minimum_required_balance: Decimal
    reported_balance: Decimal
    reserved_total: Decimal
    baseline_minimum_projected_balance: Decimal
    baseline_minimum_projected_balance_date: date
    purchase_amount_home: Decimal | None
    amount_safe_to_pay: Decimal
    earliest_safe_full_payment_date: date | None
    projected_income_in_window: Decimal
    projected_essential_expense_in_window: Decimal
    projected_flexible_expense_in_window: Decimal
    unrealized_investment_value: Decimal
    selected_plan_total_cost: Decimal | None
    selected_plan_financing_fee: Decimal | None
    required_spending_changes: tuple[SpendingChange, ...]
    blocking_obligations: tuple[UnresolvedObligation, ...]
    rejected_candidates: tuple[tuple[str, tuple[RejectionReason, ...]], ...]


@dataclass(frozen=True)
class Recommendation:
    request_id: str
    user_id: str
    amount_safe_to_pay: Decimal
    affordability_status: AffordabilityStatus
    recommended_payment_method: RecommendedMethod
    selected_candidate_id: str | None
    payment_plan: tuple[tuple[date, Decimal], ...]
    earliest_date_for_full_payment: date | None
    spending_changes: tuple[SpendingChange, ...]
    minimum_projected_balance: Decimal
    safety_margin: Decimal
    blocking_reasons: tuple[RejectionReason, ...]
    candidates: tuple[CandidateEvaluation, ...]
    explanation_facts: ExplanationFacts
    forecast_request_id: str
    forecast_window_end: date
