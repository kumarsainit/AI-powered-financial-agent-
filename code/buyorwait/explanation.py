from __future__ import annotations

import re
from decimal import Decimal

from .decision import (
    AffordabilityStatus,
    CandidateEvaluation,
    ChangeAction,
    PurchaseSpec,
    Recommendation,
    RecommendedMethod,
)
from .formatting import describe_item, format_long_date, format_money_words

_ZERO = Decimal("0")


class ExplanationError(ValueError):
    pass


def _selected(recommendation: Recommendation) -> CandidateEvaluation | None:
    return next(
        (
            evaluation
            for evaluation in recommendation.candidates
            if evaluation.candidate.candidate_id == recommendation.selected_candidate_id
        ),
        None,
    )


def _change_phrase(recommendation: Recommendation, currency: str) -> str:
    parts = []
    for change in recommendation.spending_changes:
        item = describe_item(change.description)
        if change.action is ChangeAction.STOP:
            parts.append(f"stop the {item}")
        else:
            parts.append(f"reduce the {item} to {format_money_words(change.new_amount, currency)}")
    if not parts:
        return ""
    if len(parts) == 1:
        phrase = parts[0]
    else:
        phrase = ", ".join(parts[:-1]) + " and " + parts[-1]
    return phrase[0].upper() + phrase[1:]


def _uncertainty_clause(recommendation: Recommendation) -> str:
    obligations = recommendation.explanation_facts.blocking_obligations
    if not obligations:
        return ""
    if len(obligations) == 1:
        return " One upcoming commitment has no confirmed amount yet, so review this before committing."
    return (
        f" {len(obligations)} upcoming commitments have no confirmed amount yet,"
        " so review these before committing."
    )


def _affordable_now(recommendation: Recommendation, spec: PurchaseSpec) -> str:
    currency = spec.home_currency
    amount = format_money_words(spec.requested_amount_home, currency)
    minimum = format_money_words(spec.minimum_balance_to_keep, currency)
    return (
        f"Pay {amount} today. This leaves at least {minimum} available over the next 90 days."
    )


def _affordable_with_plan(recommendation: Recommendation, spec: PurchaseSpec) -> str:
    currency = spec.home_currency
    minimum = format_money_words(spec.minimum_balance_to_keep, currency)
    selected = _selected(recommendation)
    if selected is None:
        raise ExplanationError(f"{recommendation.request_id}: a plan status requires a selected candidate")
    payments = selected.candidate.payments

    if recommendation.recommended_payment_method is RecommendedMethod.INSTALLMENTS:
        instalment = format_money_words(payments[0][1], currency)
        start = format_long_date(payments[0][0])
        return (
            f"Use {len(payments)} installments of {instalment}, starting {start}."
            f" This leaves at least {minimum} available."
        )

    if recommendation.recommended_payment_method is RecommendedMethod.PARTIAL_PAYMENT:
        first = format_money_words(payments[0][1], currency)
        second = format_money_words(payments[1][1], currency)
        when = format_long_date(payments[1][0])
        return (
            f"Pay {first} today and the remaining {second} on {when}."
            f" This completes the full request and keeps the {minimum} minimum protected."
        )

    changes = _change_phrase(recommendation, currency)
    amount = format_money_words(spec.requested_amount_home, currency)
    if changes:
        return f"{changes}, then pay {amount} today. This leaves at least {minimum} available."
    return f"Pay {amount} today. This leaves at least {minimum} available."


def _affordable_later(recommendation: Recommendation, spec: PurchaseSpec) -> str:
    currency = spec.home_currency
    amount = format_money_words(spec.requested_amount_home, currency)
    minimum = format_money_words(spec.minimum_balance_to_keep, currency)
    when = recommendation.earliest_date_for_full_payment
    if when is None:
        raise ExplanationError(f"{recommendation.request_id}: affordable_later requires a safe date")
    return (
        f"Pay {amount} in full on {format_long_date(when)}."
        f" Paying earlier would take the balance below the {minimum} minimum."
    )


def _not_affordable(recommendation: Recommendation, spec: PurchaseSpec) -> str:
    currency = spec.home_currency
    minimum = format_money_words(spec.minimum_balance_to_keep, currency)
    safe = recommendation.amount_safe_to_pay
    partial_possible = (
        spec.allows_partial_payment
        and any(method.value == "partial_payment" for method in spec.accepted_methods)
        and safe > _ZERO
    )
    if partial_possible:
        requested = format_money_words(spec.requested_amount_home, currency)
        available = format_money_words(safe, currency)
        return (
            f"Do not proceed with the {requested} request. Although {available} is available today,"
            " the full amount cannot be completed safely within 90 days."
        )
    deadline = format_long_date(spec.desired_completion_date)
    return (
        f"Do not make this payment by {deadline}."
        f" None of the available options keeps the {minimum} minimum protected."
    )


_BUILDERS = {
    AffordabilityStatus.AFFORDABLE_NOW: _affordable_now,
    AffordabilityStatus.AFFORDABLE_WITH_PLAN: _affordable_with_plan,
    AffordabilityStatus.AFFORDABLE_LATER: _affordable_later,
    AffordabilityStatus.NOT_AFFORDABLE: _not_affordable,
}

_FORBIDDEN_PATTERNS = (
    r"\bllm\b",
    r"\bai\b",
    r"\bmodel\b",
    r"\bmodels\b",
    r"\bprompt\b",
    r"\balgorithm\b",
    r"\bsimulator\b",
    r"\bsimulation\b",
    r"\bforecast engine\b",
    r"\bphase \d",
    r"\btest\b",
    r"\btests\b",
    r"\bdataset\b",
    r"\bcandidate\b",
    r"\b(?:request|user|event|payment_option)_\d+",
)


def build_explanation(recommendation: Recommendation, spec: PurchaseSpec) -> str:
    if spec.requested_amount_home is None:
        return (
            "Do not proceed with this request."
            " The requested amount cannot be expressed in the account currency with the supplied exchange rates."
        )
    builder = _BUILDERS[recommendation.affordability_status]
    text = builder(recommendation, spec) + _uncertainty_clause(recommendation)
    validate_explanation(text, recommendation)
    return text


def validate_explanation(text: str, recommendation: Recommendation) -> None:
    if not text or not text.strip():
        raise ExplanationError(f"{recommendation.request_id}: empty explanation")
    if len(text) > 400:
        raise ExplanationError(f"{recommendation.request_id}: explanation is too long")
    lowered = text.lower()
    for pattern in _FORBIDDEN_PATTERNS:
        match = re.search(pattern, lowered)
        if match is not None:
            raise ExplanationError(
                f"{recommendation.request_id}: explanation mentions '{match.group(0)}'"
            )
    if "\n" in text or "\r" in text:
        raise ExplanationError(f"{recommendation.request_id}: explanation contains a line break")
