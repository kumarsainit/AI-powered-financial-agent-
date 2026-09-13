from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .decision import AffordabilityStatus, RecommendedMethod
from .output_record import NONE_TOKEN, OUTPUT_COLUMNS

_VALID_STATUSES = {status.value for status in AffordabilityStatus}
_VALID_METHODS = {method.value for method in RecommendedMethod}
_MAX_SPENDING_CHANGES = 3


class OutputValidationError(ValueError):
    pass


@dataclass
class ValidationReport:
    rows_checked: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def fail(self, message: str) -> None:
        self.errors.append(message)


def _parse_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _check_precision(amount: Decimal) -> bool:
    exponent = amount.as_tuple().exponent
    return isinstance(exponent, int) and exponent >= -2


def validate_output_file(
    path: str | Path,
    expected_requests: dict,
    recommendations: dict | None = None,
    sample_user_ids: frozenset[str] = frozenset(),
) -> ValidationReport:
    report = ValidationReport()
    path = Path(path)
    if not path.exists():
        report.fail(f"output file does not exist: {path}")
        return report

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        rows = list(reader)

    if not rows:
        report.fail("output file is empty")
        return report

    header = tuple(rows[0])
    if header != OUTPUT_COLUMNS:
        report.fail(f"header mismatch: expected {OUTPUT_COLUMNS}, found {header}")
        return report

    body = rows[1:]
    if len(body) != len(expected_requests):
        report.fail(f"row count mismatch: expected {len(expected_requests)}, found {len(body)}")

    seen: set[str] = set()
    seen_rows: set[tuple[str, ...]] = set()

    for index, raw in enumerate(body, start=2):
        report.rows_checked += 1
        if len(raw) != len(OUTPUT_COLUMNS):
            report.fail(f"line {index}: expected {len(OUTPUT_COLUMNS)} fields, found {len(raw)}")
            continue
        row = dict(zip(OUTPUT_COLUMNS, raw))
        request_id = row["request_id"]

        if tuple(raw) in seen_rows:
            report.fail(f"line {index}: duplicate row")
        seen_rows.add(tuple(raw))

        if request_id in seen:
            report.fail(f"line {index}: duplicate request_id {request_id}")
        seen.add(request_id)

        request = expected_requests.get(request_id)
        if request is None:
            report.fail(f"line {index}: unknown request_id {request_id}")
            continue
        if getattr(request, "user_id", None) in sample_user_ids:
            report.fail(f"line {index}: sample user {request.user_id} present in production output")

        status = row["affordability_status"]
        if status not in _VALID_STATUSES:
            report.fail(f"line {index}: invalid affordability_status {status!r}")
        method = row["recommended_payment_method"]
        if method not in _VALID_METHODS:
            report.fail(f"line {index}: invalid recommended_payment_method {method!r}")

        safe_text = row["amount_safe_to_pay"]
        safe = _parse_decimal(safe_text)
        if safe is None:
            report.fail(f"line {index}: amount_safe_to_pay is not a number: {safe_text!r}")
        else:
            if safe < 0 or safe > request.requested_amount:
                report.fail(
                    f"line {index}: amount_safe_to_pay {safe} outside [0, {request.requested_amount}]"
                )
            if not _check_precision(safe):
                report.fail(f"line {index}: amount_safe_to_pay has more than two decimals")

        earliest_text = row["earliest_date_for_full_payment"]
        earliest = None
        if earliest_text:
            earliest = _parse_date(earliest_text)
            if earliest is None:
                report.fail(f"line {index}: invalid earliest_date_for_full_payment {earliest_text!r}")
            elif earliest < request.request_date:
                report.fail(f"line {index}: earliest_date_for_full_payment precedes request_date")
        if status == AffordabilityStatus.AFFORDABLE_NOW.value and earliest != request.request_date:
            report.fail(f"line {index}: affordable_now requires earliest_date_for_full_payment == request_date")
        if status == AffordabilityStatus.AFFORDABLE_LATER.value and not earliest_text:
            report.fail(f"line {index}: affordable_later requires an earliest_date_for_full_payment")

        plan_text = row["payment_plan"]
        plan: list[tuple[date, Decimal]] = []
        if plan_text != NONE_TOKEN:
            previous: date | None = None
            for part in plan_text.split("|"):
                pieces = part.split(":")
                if len(pieces) != 2:
                    report.fail(f"line {index}: malformed payment entry {part!r}")
                    continue
                when = _parse_date(pieces[0])
                amount = _parse_decimal(pieces[1])
                if when is None:
                    report.fail(f"line {index}: invalid payment date {pieces[0]!r}")
                    continue
                if amount is None or amount <= 0:
                    report.fail(f"line {index}: invalid payment amount {pieces[1]!r}")
                    continue
                if not _check_precision(amount):
                    report.fail(f"line {index}: payment amount has more than two decimals")
                if previous is not None and when < previous:
                    report.fail(f"line {index}: payment plan is not chronological")
                previous = when
                plan.append((when, amount))
        if method == RecommendedMethod.NOT_RECOMMENDED.value and plan_text != NONE_TOKEN:
            report.fail(f"line {index}: not_recommended must carry payment_plan 'none'")
        if method != RecommendedMethod.NOT_RECOMMENDED.value and not plan:
            report.fail(f"line {index}: {method} requires at least one payment")

        if method == RecommendedMethod.FULL_PAYMENT.value:
            if len(plan) != 1 or plan[0][1] != request.requested_amount:
                report.fail(f"line {index}: full_payment must be a single payment of the requested amount")
            if plan and plan[0][0] != request.request_date:
                report.fail(f"line {index}: full_payment must be scheduled on request_date")
        if method == RecommendedMethod.WAIT.value:
            if len(plan) != 1 or plan[0][1] != request.requested_amount:
                report.fail(f"line {index}: wait must be a single future payment of the requested amount")
            if plan and earliest is not None and plan[0][0] != earliest:
                report.fail(f"line {index}: wait payment must fall on earliest_date_for_full_payment")
        if method == RecommendedMethod.PARTIAL_PAYMENT.value:
            if status != AffordabilityStatus.AFFORDABLE_WITH_PLAN.value:
                report.fail(f"line {index}: partial_payment requires affordable_with_plan")
            if len(plan) != 2:
                report.fail(f"line {index}: partial_payment requires exactly two payments")
            else:
                if plan[0][1] + plan[1][1] != request.requested_amount:
                    report.fail(f"line {index}: partial payments do not sum to the requested amount")
                if safe is not None and plan[0][1] != safe:
                    report.fail(f"line {index}: first partial payment must equal amount_safe_to_pay")
                if plan[0][0] != request.request_date:
                    report.fail(f"line {index}: first partial payment must fall on request_date")
                if earliest is not None and plan[1][0] != earliest:
                    report.fail(f"line {index}: second partial payment must fall on the earliest safe date")
                if plan[1][0] > request.desired_completion_date:
                    report.fail(f"line {index}: partial payment completes after desired_completion_date")
            if not request.allows_partial_payment:
                report.fail(f"line {index}: partial_payment is not allowed for this request")
        if method == RecommendedMethod.INSTALLMENTS.value:
            if status != AffordabilityStatus.AFFORDABLE_WITH_PLAN.value:
                report.fail(f"line {index}: installments require affordable_with_plan")
            if len(plan) < 2:
                report.fail(f"line {index}: installments require at least two payments")
            total = sum((amount for _when, amount in plan), start=Decimal("0"))
            if total < request.requested_amount:
                report.fail(f"line {index}: installment total {total} is below the requested amount")

        changes_text = row["spending_changes_needed"]
        if changes_text != NONE_TOKEN:
            parts = changes_text.split("|")
            if len(parts) > _MAX_SPENDING_CHANGES:
                report.fail(f"line {index}: more than {_MAX_SPENDING_CHANGES} spending changes")
            touched: set[str] = set()
            for part in parts:
                pieces = part.split(":")
                if pieces[0] == "stop" and len(pieces) == 2:
                    event_id = pieces[1]
                elif pieces[0] == "reduce_to" and len(pieces) == 3:
                    event_id = pieces[1]
                    amount = _parse_decimal(pieces[2])
                    if amount is None or amount < 0:
                        report.fail(f"line {index}: invalid reduce_to amount {pieces[2]!r}")
                    elif not _check_precision(amount):
                        report.fail(f"line {index}: reduce_to amount has more than two decimals")
                else:
                    report.fail(f"line {index}: malformed spending change {part!r}")
                    continue
                if event_id in touched:
                    report.fail(f"line {index}: event {event_id} is changed twice")
                touched.add(event_id)

        if not row["decision_explanation"].strip():
            report.fail(f"line {index}: missing decision_explanation")

        if recommendations is not None:
            recommendation = recommendations.get(request_id)
            if recommendation is None:
                report.fail(f"line {index}: no recommendation available for reconciliation")
            else:
                if status != recommendation.affordability_status.value:
                    report.fail(f"line {index}: status does not reconcile with the decision result")
                if method != recommendation.recommended_payment_method.value:
                    report.fail(f"line {index}: method does not reconcile with the decision result")
                if safe is not None and safe != recommendation.amount_safe_to_pay:
                    report.fail(f"line {index}: amount_safe_to_pay does not reconcile with the decision result")
                expected_plan = [(when, amount) for when, amount in recommendation.payment_plan]
                if plan and expected_plan and plan != sorted(expected_plan, key=lambda i: i[0]):
                    report.fail(f"line {index}: payment plan does not reconcile with the decision result")

    missing = set(expected_requests) - seen
    if missing:
        report.fail(f"missing request ids: {sorted(missing)[:5]}")

    return report


def require_valid_output(report: ValidationReport) -> None:
    if not report.ok:
        raise OutputValidationError(
            f"{len(report.errors)} output validation error(s): " + "; ".join(report.errors[:10])
        )
