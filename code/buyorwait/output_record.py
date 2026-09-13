from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .decision import ChangeAction, PurchaseSpec, Recommendation, RecommendedMethod
from .explanation import build_explanation
from .formatting import format_iso_date, format_plan_amount, format_safe_amount

OUTPUT_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

NONE_TOKEN = "none"


class OutputAssemblyError(ValueError):
    pass


@dataclass(frozen=True)
class OutputRecord:
    request_id: str
    amount_safe_to_pay: str
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str

    def as_row(self) -> tuple[str, ...]:
        return (
            self.request_id,
            self.amount_safe_to_pay,
            self.affordability_status,
            self.recommended_payment_method,
            self.payment_plan,
            self.earliest_date_for_full_payment,
            self.spending_changes_needed,
            self.decision_explanation,
        )


def serialize_payment_plan(recommendation: Recommendation) -> str:
    if recommendation.recommended_payment_method is RecommendedMethod.NOT_RECOMMENDED:
        return NONE_TOKEN
    if not recommendation.payment_plan:
        return NONE_TOKEN
    ordered = sorted(recommendation.payment_plan, key=lambda item: item[0])
    return "|".join(
        f"{format_iso_date(when)}:{format_plan_amount(amount)}" for when, amount in ordered
    )


def serialize_spending_changes(recommendation: Recommendation) -> str:
    if not recommendation.spending_changes:
        return NONE_TOKEN
    parts = []
    for change in recommendation.spending_changes:
        if change.action is ChangeAction.STOP:
            parts.append(f"stop:{change.event_id}")
        else:
            parts.append(f"reduce_to:{change.event_id}:{format_plan_amount(change.new_amount)}")
    return "|".join(parts)


def assemble_output_record(recommendation: Recommendation, spec: PurchaseSpec) -> OutputRecord:
    if recommendation.request_id != spec.request_id:
        raise OutputAssemblyError("recommendation and purchase specification refer to different requests")

    amount = recommendation.amount_safe_to_pay
    if amount < Decimal("0"):
        raise OutputAssemblyError(f"{recommendation.request_id}: negative amount_safe_to_pay")
    if spec.requested_amount_home is not None and amount > spec.requested_amount_home:
        raise OutputAssemblyError(f"{recommendation.request_id}: amount_safe_to_pay exceeds the requested amount")

    earliest = recommendation.earliest_date_for_full_payment
    return OutputRecord(
        request_id=recommendation.request_id,
        amount_safe_to_pay=format_safe_amount(amount),
        affordability_status=recommendation.affordability_status.value,
        recommended_payment_method=recommendation.recommended_payment_method.value,
        payment_plan=serialize_payment_plan(recommendation),
        earliest_date_for_full_payment=format_iso_date(earliest) if earliest is not None else "",
        spending_changes_needed=serialize_spending_changes(recommendation),
        decision_explanation=build_explanation(recommendation, spec),
    )
