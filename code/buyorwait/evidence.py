from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class FactType(Enum):
    SALARY_RAISE = "salary_raise"
    SALARY_CUT = "salary_cut"
    SALARY_DATE_SHIFT = "salary_date_shift"
    SALARY_FIRST = "salary_first"
    SALARY_CONFIRMED = "salary_confirmed"
    INCOME_TERMINATED = "income_terminated"
    INCOME_SUSPENDED = "income_suspended"
    INCOME_RESUMED = "income_resumed"
    INCOME_BONUS_PENDING = "income_bonus_pending"
    INCOME_INVOICE_APPROVED = "income_invoice_approved"
    EXPENSE_AMOUNT = "expense_amount"
    EXPENSE_NEW_RECURRING = "expense_new_recurring"
    EXPENSE_RENT_INCREASE = "expense_rent_increase"
    EXPENSE_TERMINATED = "expense_terminated"
    EVENT_AMENDMENT = "event_amendment"
    EVENT_CANCELLATION = "event_cancellation"
    EVENT_SETTLEMENT_CONFIRMATION = "event_settlement_confirmation"
    EVENT_DELAY = "event_delay"
    REFUND_PENDING = "refund_pending"
    REFUND_SETTLED = "refund_settled"
    REFUND_PROCESSING = "refund_processing"
    TRANSFER_INTERNAL = "transfer_internal"
    DEBIT_FAILED_RETRY = "debit_failed_retry"
    INVESTMENT_UNREALIZED_GAIN = "investment_unrealized_gain"
    INVESTMENT_UNREALIZED_LOSS = "investment_unrealized_loss"
    PRIZE_CLAIM_UNVERIFIED = "prize_claim_unverified"
    PRIZE_CLAIM_PENDING = "prize_claim_pending"
    CHARGE_DISPUTED = "charge_disputed"
    FOREIGN_CURRENCY_REFUND_PROCESSING = "foreign_currency_refund_processing"
    IRRELEVANT = "irrelevant"
    UNRESOLVED = "unresolved"


class ExtractionMethod(Enum):
    DETERMINISTIC = "deterministic"
    AI_TEXT = "ai_text"
    AI_VLM = "ai_vlm"
    INFERRED = "inferred"


class FactProvenance(Enum):
    EXPLICITLY_STATED = "explicitly_stated"
    DETERMINISTICALLY_EXTRACTED = "deterministically_extracted"
    AI_EXTRACTED = "ai_extracted"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


class EvidenceSource(Enum):
    FINANCIAL_EVENTS = "financial_events"
    MESSAGE = "message"
    IMAGE = "image"


class EvidenceStatus(Enum):
    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    UNRESOLVED = "unresolved"


class ConflictReason(Enum):
    EXPLICIT_CANCELLATION = "explicit_cancellation"
    NEWER_SAME_SOURCE = "newer_same_source"
    SETTLED_OVER_ESTIMATE = "settled_over_estimate"
    FINANCIALLY_SAFER = "financially_safer"
    AMBIGUOUS_AMOUNT = "ambiguous_amount"
    UNSUPPORTED_CLAIM = "unsupported_claim"


@dataclass(frozen=True)
class EvidenceFact:
    fact_id: str
    fact_type: FactType
    source_type: EvidenceSource
    source_id: str
    user_id: str
    request_id: str | None
    event_id: str | None
    extraction_method: ExtractionMethod
    provenance: FactProvenance
    status: EvidenceStatus
    amount: Decimal | None
    currency: str | None
    effective_date: date | None
    end_date: date | None
    recurrence: str | None
    category: str | None
    description: str
    is_trusted: bool
    ambiguity_note: str | None
    raw_source_ref: str


@dataclass(frozen=True)
class ConflictRecord:
    fact_a: EvidenceFact
    fact_b: EvidenceFact
    resolved: bool
    selected_fact_id: str | None
    reason: ConflictReason | None
    note: str


@dataclass(frozen=True)
class EvidenceBundle:
    request_id: str
    user_id: str
    facts: tuple[EvidenceFact, ...]
    conflicts: tuple[ConflictRecord, ...]
    unresolved_count: int
    has_untrusted_content: bool
    extraction_methods_used: tuple[ExtractionMethod, ...]
