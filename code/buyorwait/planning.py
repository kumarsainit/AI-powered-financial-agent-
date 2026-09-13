from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
from itertools import combinations
from typing import Sequence

from .bundle import RequestBundle
from .decision import (
    AffordabilityStatus,
    CandidateEvaluation,
    CandidateKind,
    CandidatePlan,
    ChangeAction,
    ExplanationFacts,
    GateResult,
    PaymentOptionModel,
    PurchaseSpec,
    Recommendation,
    RecommendedMethod,
    RejectionReason,
    SpendingChange,
)
from .domain import Direction, Flexibility, PaymentMethod
from .errors import ExchangeRateNotFoundError
from .financial_state import (
    CashFlowKind,
    Certainty,
    ConversionStatus,
    FinancialStateForecast,
    ProjectedCashFlowEvent,
    Provenance,
    ProvenanceKind,
    SpendingClass,
    UnresolvedObligation,
)
from .simulator import ordering_key, simulate

CENT = Decimal("0.01")
_ZERO = Decimal("0")
_MAX_SPENDING_CHANGES = 3
_MAX_CHANGE_CANDIDATES = 6
_PURCHASE_CATEGORY = "requested_purchase"


@dataclass(frozen=True)
class DecisionConfig:
    max_spending_changes: int = _MAX_SPENDING_CHANGES
    max_change_candidates: int = _MAX_CHANGE_CANDIDATES
    block_on_unresolved_obligations: bool = False


@dataclass(frozen=True)
class SimulationOutcome:
    minimum_projected_balance: Decimal
    minimum_projected_balance_date: date
    safety_margin: Decimal
    breaches_minimum: bool
    ending_balance: Decimal


def floor_to_cent(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_DOWN)


def _payment_event(when: date, amount: Decimal, spec: PurchaseSpec, index: int) -> ProjectedCashFlowEvent:
    return ProjectedCashFlowEvent(
        event_id=f"plan:{spec.request_id}:{index}",
        kind=CashFlowKind.CONFIRMED_FUTURE,
        certainty=Certainty.CONFIRMED,
        spending_class=SpendingClass.NOT_APPLICABLE,
        when=when,
        direction=Direction.DEBIT,
        original_amount=amount,
        original_currency=spec.home_currency,
        amount_home=amount,
        home_currency=spec.home_currency,
        conversion_status=ConversionStatus.SAME_CURRENCY,
        category=_PURCHASE_CATEGORY,
        description=f"candidate payment for {spec.request_id}",
        provenance=Provenance(
            kind=ProvenanceKind.LEDGER_EVENT,
            reference_id=spec.request_id,
            detail="candidate plan payment injected into the Phase 4 simulation",
        ),
        source_cadence=None,
        source_event_ids=(),
        is_reducible=False,
        is_stoppable=False,
        is_protected=False,
    )


def apply_spending_changes(
    events: Sequence[ProjectedCashFlowEvent],
    changes: Sequence[SpendingChange],
) -> tuple[ProjectedCashFlowEvent, ...]:
    if not changes:
        return tuple(events)
    stopped = {c.event_id for c in changes if c.action is ChangeAction.STOP}
    reduced = {c.event_id: c for c in changes if c.action is ChangeAction.REDUCE_TO}

    adjusted: list[ProjectedCashFlowEvent] = []
    for event in events:
        sources = set(event.source_event_ids)
        if sources & stopped:
            continue
        match = next((reduced[event_id] for event_id in reduced if event_id in sources), None)
        if match is None or event.original_amount is None or event.amount_home is None:
            adjusted.append(event)
            continue
        if event.original_amount == 0:
            adjusted.append(event)
            continue
        conversion_ratio = event.amount_home / event.original_amount
        adjusted.append(
            replace(
                event,
                original_amount=match.new_amount,
                amount_home=(match.new_amount * conversion_ratio).quantize(CENT),
                description=f"{event.description} (reduced to {match.new_amount})",
            )
        )
    return tuple(adjusted)


def simulate_plan(
    forecast: FinancialStateForecast,
    spec: PurchaseSpec,
    payments: Sequence[tuple[date, Decimal]],
    changes: Sequence[SpendingChange] = (),
) -> SimulationOutcome:
    events = list(apply_spending_changes(forecast.events, changes))
    for index, (when, amount) in enumerate(payments, start=1):
        if forecast.window.contains(when):
            events.append(_payment_event(when, amount, spec, index))
    events.sort(key=ordering_key)
    states = simulate(
        forecast.starting_state.available_cash,
        events,
        forecast.window,
        spec.minimum_balance_to_keep,
    )
    low_state = min(states, key=lambda state: (state.closing_balance, state.day_index))
    return SimulationOutcome(
        minimum_projected_balance=low_state.closing_balance,
        minimum_projected_balance_date=low_state.when,
        safety_margin=low_state.closing_balance - spec.minimum_balance_to_keep,
        breaches_minimum=low_state.closing_balance < spec.minimum_balance_to_keep,
        ending_balance=states[-1].closing_balance,
    )


def normalize_request(bundle: RequestBundle, forecast: FinancialStateForecast) -> PurchaseSpec:
    request = bundle.request
    profile = bundle.profile
    requested_currency = profile.home_currency
    amount_home: Decimal | None = request.requested_amount
    conversion = ConversionStatus.SAME_CURRENCY
    return PurchaseSpec(
        request_id=request.request_id,
        user_id=request.user_id,
        request_date=request.request_date,
        desired_completion_date=request.desired_completion_date,
        requested_amount_original=request.requested_amount,
        requested_currency=requested_currency,
        requested_amount_home=amount_home,
        home_currency=profile.home_currency,
        conversion_status=conversion,
        allows_partial_payment=request.allows_partial_payment,
        accepted_methods=profile.payment_methods_user_will_consider,
        max_installment_months=profile.max_installment_months,
        minimum_balance_to_keep=profile.minimum_balance_to_keep,
        available_cash=forecast.starting_state.available_cash,
        request_type=request.request_type.value,
    )


def normalize_request_in_currency(
    bundle: RequestBundle,
    forecast: FinancialStateForecast,
    requested_currency: str,
) -> PurchaseSpec:
    spec = normalize_request(bundle, forecast)
    if requested_currency == spec.home_currency:
        return spec
    try:
        converted = bundle.exchange_rates.convert(
            spec.requested_amount_original,
            spec.request_date,
            requested_currency,
            spec.home_currency,
        )
        conversion = ConversionStatus.CONVERTED
    except ExchangeRateNotFoundError:
        converted = None
        conversion = ConversionStatus.UNRESOLVED_RATE
    return replace(
        spec,
        requested_currency=requested_currency,
        requested_amount_home=converted,
        conversion_status=conversion,
    )


def build_payment_option_models(
    bundle: RequestBundle,
    spec: PurchaseSpec,
) -> tuple[PaymentOptionModel, ...]:
    models: list[PaymentOptionModel] = []
    for option in sorted(bundle.payment_options, key=lambda o: o.payment_option_id):
        payments: list[tuple[date, Decimal]] = []
        step = option.payment_frequency_days or 0
        for index in range(option.number_of_payments):
            when = option.first_payment_date + timedelta(days=step * index)
            payments.append((when, option.payment_amount))

        eligibility_reason: RejectionReason | None = None
        eligible = True
        if option.payment_method is PaymentMethod.INSTALLMENTS:
            if spec.max_installment_months is None:
                eligible = False
                eligibility_reason = RejectionReason.PAYMENT_METHOD_INELIGIBLE
            elif option.number_of_payments > spec.max_installment_months:
                eligible = False
                eligibility_reason = RejectionReason.INSTALLMENT_TERM_TOO_LONG

        models.append(
            PaymentOptionModel(
                payment_option_id=option.payment_option_id,
                payment_method=option.payment_method,
                upfront_amount=payments[0][1] if payments else _ZERO,
                payments=tuple(payments),
                number_of_payments=option.number_of_payments,
                total_payable_amount=option.total_payable_amount,
                financing_fee=option.financing_fee,
                first_payment_date=option.first_payment_date,
                last_payment_date=payments[-1][0] if payments else option.first_payment_date,
                payment_frequency_days=option.payment_frequency_days,
                is_eligible=eligible,
                eligibility_reason=eligibility_reason,
                is_preferred=option.payment_method in spec.accepted_methods,
                provenance=f"request_payment_options.csv:{option.payment_option_id}",
            )
        )
    return tuple(models)


def compute_safe_amount(forecast: FinancialStateForecast, cap: Decimal) -> Decimal:
    margin = forecast.minimum_projected_balance - forecast.starting_state.minimum_balance_to_keep
    if margin <= 0:
        return _ZERO
    return min(cap, floor_to_cent(margin))


def suffix_minimums(forecast: FinancialStateForecast) -> tuple[Decimal, ...]:
    lows = [state.closing_balance for state in forecast.daily_states]
    result = [Decimal("0")] * len(lows)
    running = lows[-1]
    result[-1] = running
    for index in range(len(lows) - 2, -1, -1):
        running = min(running, lows[index])
        result[index] = running
    return tuple(result)


def prefix_minimums(forecast: FinancialStateForecast) -> tuple[Decimal, ...]:
    lows = [state.closing_balance for state in forecast.daily_states]
    result = []
    running = lows[0]
    for low in lows:
        running = min(running, low)
        result.append(running)
    return tuple(result)


def earliest_safe_full_payment_date(
    forecast: FinancialStateForecast,
    amount: Decimal,
) -> date | None:
    minimum = forecast.starting_state.minimum_balance_to_keep
    states = forecast.daily_states
    prefix = prefix_minimums(forecast)
    suffix = suffix_minimums(forecast)

    for index, state in enumerate(states):
        if index > 0 and prefix[index - 1] < minimum:
            return None
        if suffix[index] - amount < minimum:
            continue
        return state.when
    return None


def build_spending_change_options(
    bundle: RequestBundle,
    forecast: FinancialStateForecast,
    config: DecisionConfig,
) -> tuple[SpendingChange, ...]:
    projected_sources: dict[str, list[ProjectedCashFlowEvent]] = {}
    for event in forecast.events:
        if event.direction is not Direction.DEBIT:
            continue
        for source in event.source_event_ids:
            projected_sources.setdefault(source, []).append(event)

    by_category: dict[str, SpendingChange] = {}
    for candidate in sorted(bundle.spending_change_candidates, key=lambda c: c.event_id):
        if candidate.is_protected or candidate.current_amount is None:
            continue
        if candidate.event_id not in projected_sources:
            continue
        occurrences = projected_sources[candidate.event_id]
        monthly_saving = max((e.amount_home or _ZERO) for e in occurrences)

        action: ChangeAction | None = None
        new_amount = _ZERO
        if (
            candidate.user_permits_stop
            and candidate.flexibility in (Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE)
        ):
            action = ChangeAction.STOP
            new_amount = _ZERO
        elif (
            candidate.user_permits_reduce
            and candidate.flexibility in (Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE)
            and candidate.minimum_allowed_amount is not None
            and candidate.minimum_allowed_amount < candidate.current_amount
        ):
            action = ChangeAction.REDUCE_TO
            new_amount = candidate.minimum_allowed_amount
        if action is None:
            continue

        source_event = next((e for e in bundle.events if e.event_id == candidate.event_id), None)
        change = SpendingChange(
            action=action,
            event_id=candidate.event_id,
            category=candidate.category,
            description=source_event.description if source_event is not None else candidate.category,
            current_amount=candidate.current_amount,
            new_amount=new_amount,
            minimum_allowed_amount=candidate.minimum_allowed_amount,
            monthly_saving=monthly_saving - (monthly_saving * new_amount / candidate.current_amount)
            if candidate.current_amount
            else _ZERO,
            source_event_ids=tuple(sorted({s for e in occurrences for s in e.source_event_ids})),
        )
        existing = by_category.get(candidate.category)
        if existing is None or candidate.event_id > existing.event_id:
            by_category[candidate.category] = change

    ordered = sorted(by_category.values(), key=lambda c: (-c.monthly_saving, c.event_id))
    return tuple(ordered[: config.max_change_candidates])


def find_minimal_spending_changes(
    forecast: FinancialStateForecast,
    spec: PurchaseSpec,
    payments: Sequence[tuple[date, Decimal]],
    options: Sequence[SpendingChange],
    config: DecisionConfig,
) -> tuple[SpendingChange, ...] | None:
    if not options:
        return None
    base = simulate_plan(forecast, spec, payments)
    deficit = spec.minimum_balance_to_keep - base.minimum_projected_balance
    if deficit <= 0:
        return ()

    benefits: dict[str, Decimal] = {}
    for option in options:
        outcome = simulate_plan(forecast, spec, payments, (option,))
        benefits[option.event_id] = outcome.minimum_projected_balance - base.minimum_projected_balance

    for size in range(1, min(config.max_spending_changes, len(options)) + 1):
        subsets = []
        for subset in combinations(options, size):
            optimistic = sum((benefits[c.event_id] for c in subset), start=_ZERO)
            if optimistic < deficit:
                continue
            total_reduction = sum((c.current_amount - c.new_amount for c in subset), start=_ZERO)
            subsets.append((total_reduction, tuple(c.event_id for c in subset), subset))
        for _reduction, _ids, subset in sorted(subsets, key=lambda item: (item[0], item[1])):
            outcome = simulate_plan(forecast, spec, payments, subset)
            if not outcome.breaches_minimum:
                return subset
    return None


def _gate(
    method: PaymentMethod | None,
    spec: PurchaseSpec,
    option: PaymentOptionModel | None,
    financially_safe: bool,
) -> GateResult:
    eligibility = True if option is None else option.is_eligible
    preference = True if method is None else method in spec.accepted_methods
    return GateResult(
        financial_capacity=financially_safe,
        method_eligibility=eligibility,
        user_preference=preference,
    )


def _completion_date(payments: Sequence[tuple[date, Decimal]]) -> date | None:
    return max((when for when, _ in payments), default=None)


def _make_candidate(
    candidate_id: str,
    kind: CandidateKind,
    method: RecommendedMethod,
    payments: Sequence[tuple[date, Decimal]],
    spec: PurchaseSpec,
    option: PaymentOptionModel | None,
    changes: Sequence[SpendingChange],
    provenance: str,
) -> CandidatePlan:
    total = sum((amount for _, amount in payments), start=_ZERO)
    completion = _completion_date(payments)
    return CandidatePlan(
        candidate_id=candidate_id,
        kind=kind,
        method=method,
        payment_option_id=option.payment_option_id if option else None,
        payments=tuple(payments),
        total_paid=total,
        financing_fee=option.financing_fee if option else _ZERO,
        spending_changes=tuple(changes),
        completes_request=completion is not None and completion <= spec.desired_completion_date,
        completion_date=completion,
        provenance=provenance,
    )


def evaluate_candidate(
    candidate: CandidatePlan,
    forecast: FinancialStateForecast,
    spec: PurchaseSpec,
    option: PaymentOptionModel | None,
) -> CandidateEvaluation:
    outcome = simulate_plan(forecast, spec, candidate.payments, candidate.spending_changes)
    reasons: list[RejectionReason] = []

    if outcome.breaches_minimum:
        reasons.append(RejectionReason.MINIMUM_BALANCE_VIOLATION)
        if outcome.minimum_projected_balance_date == spec.request_date:
            reasons.append(RejectionReason.INSUFFICIENT_CURRENT_SURPLUS)
        else:
            reasons.append(RejectionReason.FUTURE_CASH_FLOW_DEFICIT)
        if candidate.kind is CandidateKind.INSTALLMENTS:
            reasons.append(RejectionReason.INSTALLMENT_UNSAFE)

    if option is not None and not option.is_eligible and option.eligibility_reason is not None:
        reasons.append(option.eligibility_reason)

    method_map = {
        RecommendedMethod.FULL_PAYMENT: PaymentMethod.FULL_PAYMENT,
        RecommendedMethod.WAIT: PaymentMethod.FULL_PAYMENT,
        RecommendedMethod.PARTIAL_PAYMENT: PaymentMethod.PARTIAL_PAYMENT,
        RecommendedMethod.INSTALLMENTS: PaymentMethod.INSTALLMENTS,
    }
    required_method = method_map.get(candidate.method)
    preference_ok = required_method is None or required_method in spec.accepted_methods
    if not preference_ok:
        reasons.append(RejectionReason.PAYMENT_METHOD_NOT_PREFERRED)

    if not candidate.completes_request and len(candidate.payments) > 1:
        reasons.append(RejectionReason.COMPLETION_AFTER_DEADLINE)

    if (
        candidate.kind is CandidateKind.INSTALLMENTS
        and spec.requested_amount_home is not None
        and candidate.total_paid < spec.requested_amount_home
    ):
        reasons.append(RejectionReason.INSTALLMENT_TOTAL_BELOW_REQUEST)

    if spec.requested_amount_home is None:
        reasons.append(RejectionReason.UNRESOLVED_CURRENCY_CONVERSION)

    inside = sum(1 for when, _ in candidate.payments if forecast.window.contains(when))
    outside = len(candidate.payments) - inside

    return CandidateEvaluation(
        candidate=candidate,
        gates=GateResult(
            financial_capacity=not outcome.breaches_minimum,
            method_eligibility=option.is_eligible if option is not None else True,
            user_preference=preference_ok,
        ),
        minimum_projected_balance=outcome.minimum_projected_balance,
        minimum_projected_balance_date=outcome.minimum_projected_balance_date,
        safety_margin=outcome.safety_margin,
        breaches_minimum=outcome.breaches_minimum,
        payments_inside_window=inside,
        payments_outside_window=outside,
        rejection_reasons=tuple(dict.fromkeys(reasons)),
    )


def generate_candidates(
    bundle: RequestBundle,
    forecast: FinancialStateForecast,
    spec: PurchaseSpec,
    options: Sequence[PaymentOptionModel],
    config: DecisionConfig,
) -> tuple[CandidateEvaluation, ...]:
    amount = spec.requested_amount_home
    evaluations: list[CandidateEvaluation] = []
    if amount is None:
        return ()

    change_options = build_spending_change_options(bundle, forecast, config)
    full_option = next(
        (o for o in options if o.payment_method is PaymentMethod.FULL_PAYMENT), None
    )
    earliest_full = earliest_safe_full_payment_date(forecast, amount)

    if full_option is not None:
        today_payments = ((spec.request_date, amount),)
        base = _make_candidate(
            "full_today",
            CandidateKind.FULL_PAYMENT_TODAY,
            RecommendedMethod.FULL_PAYMENT,
            today_payments,
            spec,
            full_option,
            (),
            full_option.provenance,
        )
        base_evaluation = evaluate_candidate(base, forecast, spec, full_option)
        evaluations.append(base_evaluation)

        if base_evaluation.breaches_minimum:
            changes = find_minimal_spending_changes(
                forecast, spec, today_payments, change_options, config
            )
            if changes:
                adjusted = _make_candidate(
                    "full_today_with_changes",
                    CandidateKind.FULL_PAYMENT_TODAY,
                    RecommendedMethod.FULL_PAYMENT,
                    today_payments,
                    spec,
                    full_option,
                    changes,
                    f"{full_option.provenance}+spending_changes",
                )
                evaluations.append(evaluate_candidate(adjusted, forecast, spec, full_option))

        if earliest_full is not None and earliest_full > spec.request_date:
            later = _make_candidate(
                "full_later",
                CandidateKind.FULL_PAYMENT_LATER,
                RecommendedMethod.WAIT,
                ((earliest_full, amount),),
                spec,
                full_option,
                (),
                f"{full_option.provenance}+earliest_safe_date",
            )
            evaluations.append(evaluate_candidate(later, forecast, spec, full_option))

    safe_now = compute_safe_amount(forecast, amount)
    if (
        spec.allows_partial_payment
        and PaymentMethod.PARTIAL_PAYMENT in spec.accepted_methods
        and _ZERO < safe_now < amount
        and earliest_full is not None
    ):
        remaining = amount - safe_now
        partial = _make_candidate(
            "partial",
            CandidateKind.PARTIAL_PAYMENT,
            RecommendedMethod.PARTIAL_PAYMENT,
            ((spec.request_date, safe_now), (earliest_full, remaining)),
            spec,
            None,
            (),
            "derived:amount_safe_to_pay+earliest_safe_full_payment_date",
        )
        evaluations.append(evaluate_candidate(partial, forecast, spec, None))

    for option in options:
        if option.payment_method is not PaymentMethod.INSTALLMENTS:
            continue
        candidate = _make_candidate(
            f"installments:{option.payment_option_id}",
            CandidateKind.INSTALLMENTS,
            RecommendedMethod.INSTALLMENTS,
            option.payments,
            spec,
            option,
            (),
            option.provenance,
        )
        evaluation = evaluate_candidate(candidate, forecast, spec, option)
        evaluations.append(evaluation)

        if evaluation.breaches_minimum and option.is_eligible and option.is_preferred:
            changes = find_minimal_spending_changes(
                forecast, spec, option.payments, change_options, config
            )
            if changes:
                adjusted = _make_candidate(
                    f"installments:{option.payment_option_id}+changes",
                    CandidateKind.INSTALLMENTS,
                    RecommendedMethod.INSTALLMENTS,
                    option.payments,
                    spec,
                    option,
                    changes,
                    f"{option.provenance}+spending_changes",
                )
                evaluations.append(evaluate_candidate(adjusted, forecast, spec, option))

    return tuple(evaluations)


def selection_key(evaluation: CandidateEvaluation) -> tuple:
    candidate = evaluation.candidate
    return (
        0 if candidate.completes_request else 1,
        0 if not candidate.spending_changes else 1,
        candidate.total_paid,
        candidate.payments[0][0] if candidate.payments else date.max,
        len(candidate.payments),
        candidate.payment_option_id or "",
        candidate.candidate_id,
    )


def _status_for(candidate: CandidatePlan) -> tuple[AffordabilityStatus, RecommendedMethod]:
    if candidate.kind is CandidateKind.FULL_PAYMENT_TODAY:
        if candidate.spending_changes:
            return AffordabilityStatus.AFFORDABLE_WITH_PLAN, RecommendedMethod.FULL_PAYMENT
        return AffordabilityStatus.AFFORDABLE_NOW, RecommendedMethod.FULL_PAYMENT
    if candidate.kind is CandidateKind.FULL_PAYMENT_LATER:
        return AffordabilityStatus.AFFORDABLE_LATER, RecommendedMethod.WAIT
    if candidate.kind is CandidateKind.PARTIAL_PAYMENT:
        return AffordabilityStatus.AFFORDABLE_WITH_PLAN, RecommendedMethod.PARTIAL_PAYMENT
    if candidate.kind is CandidateKind.INSTALLMENTS:
        return AffordabilityStatus.AFFORDABLE_WITH_PLAN, RecommendedMethod.INSTALLMENTS
    return AffordabilityStatus.NOT_AFFORDABLE, RecommendedMethod.NOT_RECOMMENDED


def blocking_unresolved_obligations(
    forecast: FinancialStateForecast,
) -> tuple[UnresolvedObligation, ...]:
    return tuple(
        obligation
        for obligation in forecast.unresolved_obligations
        if obligation.direction is Direction.DEBIT
        and (obligation.effective_date is None or forecast.window.contains(obligation.effective_date))
    )


def build_recommendation(
    bundle: RequestBundle,
    forecast: FinancialStateForecast,
    config: DecisionConfig = DecisionConfig(),
    spec: PurchaseSpec | None = None,
) -> Recommendation:
    spec = spec or normalize_request(bundle, forecast)
    options = build_payment_option_models(bundle, spec)
    evaluations = generate_candidates(bundle, forecast, spec, options, config)

    cap = spec.requested_amount_home if spec.requested_amount_home is not None else _ZERO
    amount_safe_to_pay = compute_safe_amount(forecast, cap)
    earliest_full = (
        earliest_safe_full_payment_date(forecast, spec.requested_amount_home)
        if spec.requested_amount_home is not None
        else None
    )

    obligations = blocking_unresolved_obligations(forecast)
    selectable = [e for e in evaluations if e.is_selectable]
    if config.block_on_unresolved_obligations and obligations:
        selectable = []

    blocking_reasons: list[RejectionReason] = []
    if selectable:
        selected = min(selectable, key=selection_key)
        status, method = _status_for(selected.candidate)
        selected_candidate = selected.candidate
        minimum_balance = selected.minimum_projected_balance
        safety_margin = selected.safety_margin
    else:
        selected = None
        selected_candidate = None
        status, method = AffordabilityStatus.NOT_AFFORDABLE, RecommendedMethod.NOT_RECOMMENDED
        minimum_balance = forecast.minimum_projected_balance
        safety_margin = forecast.safety_margin
        seen: list[RejectionReason] = []
        for evaluation in evaluations:
            for reason in evaluation.rejection_reasons:
                if reason not in seen:
                    seen.append(reason)
        if not evaluations:
            seen.append(RejectionReason.NO_SAFE_DATE_IN_HORIZON)
        blocking_reasons = seen

    if obligations:
        blocking_reasons.append(RejectionReason.BLOCKING_UNRESOLVED_OBLIGATION)

    facts = ExplanationFacts(
        available_cash_at_request_date=forecast.starting_state.available_cash,
        minimum_required_balance=spec.minimum_balance_to_keep,
        reported_balance=forecast.starting_state.reported_balance,
        reserved_total=forecast.starting_state.reserved_total,
        baseline_minimum_projected_balance=forecast.minimum_projected_balance,
        baseline_minimum_projected_balance_date=forecast.minimum_projected_balance_date,
        purchase_amount_home=spec.requested_amount_home,
        amount_safe_to_pay=amount_safe_to_pay,
        earliest_safe_full_payment_date=earliest_full,
        projected_income_in_window=forecast.cumulative_future_income,
        projected_essential_expense_in_window=forecast.cumulative_future_essential_expense,
        projected_flexible_expense_in_window=forecast.cumulative_future_flexible_expense,
        unrealized_investment_value=forecast.starting_state.unrealized_investment_value,
        selected_plan_total_cost=selected_candidate.total_paid if selected_candidate else None,
        selected_plan_financing_fee=selected_candidate.financing_fee if selected_candidate else None,
        required_spending_changes=selected_candidate.spending_changes if selected_candidate else (),
        blocking_obligations=obligations,
        rejected_candidates=tuple(
            (e.candidate.candidate_id, e.rejection_reasons)
            for e in evaluations
            if e.rejection_reasons
        ),
    )

    return Recommendation(
        request_id=spec.request_id,
        user_id=spec.user_id,
        amount_safe_to_pay=amount_safe_to_pay,
        affordability_status=status,
        recommended_payment_method=method,
        selected_candidate_id=selected_candidate.candidate_id if selected_candidate else None,
        payment_plan=selected_candidate.payments if selected_candidate else (),
        earliest_date_for_full_payment=earliest_full,
        spending_changes=selected_candidate.spending_changes if selected_candidate else (),
        minimum_projected_balance=minimum_balance,
        safety_margin=safety_margin,
        blocking_reasons=tuple(dict.fromkeys(blocking_reasons)),
        candidates=evaluations,
        explanation_facts=facts,
        forecast_request_id=forecast.request_id,
        forecast_window_end=forecast.window.end_date,
    )
