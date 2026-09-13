from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.bundle import RequestBundle
from buyorwait.currency import ExchangeRateTable
from buyorwait.domain import (
    Direction,
    EventStatus,
    EventType,
    ExchangeRateRecord,
    FinancialEvent,
    Flexibility,
    PaymentMethod,
    Request,
    RequestType,
    UserFinancialProfile,
)
from buyorwait.estimators import AmountStatistic, estimate_amount
from buyorwait.evidence import (
    EvidenceBundle,
    EvidenceFact,
    EvidenceSource,
    EvidenceStatus,
    ExtractionMethod,
    FactProvenance,
    FactType,
)
from buyorwait.financial_state import (
    CashFlowKind,
    Certainty,
    ConversionStatus,
    ExclusionReason,
    ForecastWindow,
    SpendingClass,
)
from buyorwait.forecast import build_financial_state
from buyorwait.forecast_config import ForecastConfig
from buyorwait.invariants import InvariantViolation, check_forecast_invariants
from buyorwait.lifecycle import resolve_lifecycle
from buyorwait.recurrence import RecurrenceDetectorConfig, detect_recurrence
from buyorwait.spending_change import build_spending_change_candidates
from buyorwait.spending_history import compute_spending_observations

REQUEST_DATE = date(2025, 8, 1)
HOME = "IDR"


def profile(
    balance: str = "10000",
    minimum: str = "1000",
    protect: tuple[str, ...] = ("rent", "groceries"),
    reduce: tuple[str, ...] = ("streaming",),
    stop: tuple[str, ...] = ("streaming",),
    home_currency: str = HOME,
) -> UserFinancialProfile:
    return UserFinancialProfile(
        user_id="user_x",
        home_currency=home_currency,
        current_available_balance=Decimal(balance),
        minimum_balance_to_keep=Decimal(minimum),
        financial_priorities=("emergency_savings",),
        expense_categories_to_protect=protect,
        expense_categories_user_is_willing_to_reduce=reduce,
        expense_categories_user_is_willing_to_stop=stop,
        payment_methods_user_will_consider=(PaymentMethod.FULL_PAYMENT,),
        max_installment_months=None,
    )


def request(request_date: date = REQUEST_DATE) -> Request:
    return Request(
        request_id="request_x",
        user_id="user_x",
        request_date=request_date,
        request_type=RequestType.PURCHASE,
        requested_amount=Decimal("500"),
        desired_completion_date=request_date + timedelta(days=60),
        allows_partial_payment=True,
        request_text="test request",
    )


def event(
    event_id: str,
    when: date,
    amount: str | None,
    *,
    event_type: EventType = EventType.EXPENSE,
    direction: Direction = Direction.DEBIT,
    status: EventStatus = EventStatus.SETTLED,
    category: str = "groceries",
    currency: str = HOME,
    linked: str | None = None,
    flexibility: Flexibility = Flexibility.FIXED,
    settlement: date | None = None,
    minimum_allowed: str | None = None,
) -> FinancialEvent:
    return FinancialEvent(
        event_id=event_id,
        user_id="user_x",
        event_type=event_type,
        description=f"{category} {event_id}",
        category=category,
        direction=direction,
        amount=None if amount is None else Decimal(amount),
        currency=currency,
        event_date=when,
        settlement_date=settlement or when,
        status=status,
        linked_event_id=linked,
        flexibility=flexibility,
        minimum_allowed_amount=None if minimum_allowed is None else Decimal(minimum_allowed),
    )


def rates(records: tuple[ExchangeRateRecord, ...] = ()) -> ExchangeRateTable:
    return ExchangeRateTable.from_records(records)


def bundle(
    events: tuple[FinancialEvent, ...],
    user_profile: UserFinancialProfile | None = None,
    request_obj: Request | None = None,
    rate_table: ExchangeRateTable | None = None,
    recurrence_config: RecurrenceDetectorConfig = RecurrenceDetectorConfig(),
) -> RequestBundle:
    user_profile = user_profile or profile()
    request_obj = request_obj or request()
    chains = resolve_lifecycle(events)
    included = frozenset(i for chain in chains for i in chain.included_event_ids())
    included_events = tuple(e for e in events if e.event_id in included)
    candidates = detect_recurrence(included_events, recurrence_config)
    return RequestBundle(
        request=request_obj,
        profile=user_profile,
        events=events,
        lifecycle_chains=chains,
        included_event_ids=included,
        recurrence_candidates=candidates,
        spending_observations=compute_spending_observations(included_events),
        spending_change_candidates=build_spending_change_candidates(included_events, user_profile, candidates),
        payment_options=(),
        messages=(),
        images=(),
        exchange_rates=rate_table or rates(),
    )


def evidence_bundle(*facts: EvidenceFact) -> EvidenceBundle:
    return EvidenceBundle(
        request_id="request_x",
        user_id="user_x",
        facts=facts,
        conflicts=(),
        unresolved_count=sum(1 for f in facts if f.status is EvidenceStatus.UNRESOLVED),
        has_untrusted_content=any(not f.is_trusted for f in facts),
        extraction_methods_used=(ExtractionMethod.DETERMINISTIC,),
    )


def fact(
    fact_type: FactType,
    *,
    amount: str | None = None,
    effective: date | None = None,
    currency: str | None = HOME,
    category: str | None = None,
    recurrence: str | None = None,
    status: EvidenceStatus = EvidenceStatus.CONFIRMED,
    event_id: str | None = None,
    trusted: bool = True,
    fact_id: str = "fact_1",
) -> EvidenceFact:
    return EvidenceFact(
        fact_id=fact_id,
        fact_type=fact_type,
        source_type=EvidenceSource.MESSAGE,
        source_id="message_1",
        user_id="user_x",
        request_id="request_x",
        event_id=event_id,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        provenance=FactProvenance.EXPLICITLY_STATED,
        status=status,
        amount=None if amount is None else Decimal(amount),
        currency=currency,
        effective_date=effective,
        end_date=None,
        recurrence=recurrence,
        category=category,
        description=f"{fact_type.value} test fact",
        is_trusted=trusted,
        ambiguity_note=None,
        raw_source_ref="message_1",
        created_at=datetime(2025, 7, 30, 9, 0, 0),
    )


def monthly_series(prefix: str, category: str, amounts: list[str], **kwargs) -> tuple[FinancialEvent, ...]:
    start = date(2025, 2, 5)
    events = []
    for index, amount in enumerate(amounts):
        month = start.month + index
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        events.append(event(f"{prefix}_{index}", date(year, month, start.day), amount, category=category, **kwargs))
    return tuple(events)


def test_request_date_starting_balance_uses_profile_balance():
    forecast = build_financial_state(bundle(monthly_series("e", "groceries", ["100", "110", "120"])))
    assert forecast.starting_state.reported_balance == Decimal("10000")
    assert forecast.starting_state.available_cash == Decimal("10000")
    assert forecast.starting_state.as_of == REQUEST_DATE


def test_lifecycle_normalized_starting_state_excludes_superseded_leg():
    events = (
        event("e1", date(2025, 7, 1), "50", status=EventStatus.PENDING, linked="e2"),
        event("e2", date(2025, 7, 2), "50", status=EventStatus.SETTLED),
    )
    forecast = build_financial_state(bundle(events))
    reasons = {item.reason for item in forecast.starting_state.excluded_items}
    assert ExclusionReason.SUPERSEDED_DUPLICATE in reasons
    assert forecast.starting_state.reserved_total == Decimal("0")


def test_failed_transaction_is_never_a_cash_outflow():
    events = (event("e1", date(2025, 7, 10), "300", status=EventStatus.FAILED),)
    forecast = build_financial_state(bundle(events))
    assert forecast.starting_state.available_cash == Decimal("10000")
    assert any(i.reason is ExclusionReason.FAILED_TRANSACTION for i in forecast.starting_state.excluded_items)


def test_cancelled_authorization_is_never_a_cash_outflow():
    events = (event("e1", date(2025, 7, 10), "300", status=EventStatus.CANCELLED),)
    forecast = build_financial_state(bundle(events))
    assert forecast.starting_state.available_cash == Decimal("10000")
    assert any(i.reason is ExclusionReason.CANCELLED_TRANSACTION for i in forecast.starting_state.excluded_items)


def test_settled_refund_leg_is_not_reserved():
    events = (
        event("e1", date(2025, 6, 26), "80", status=EventStatus.SETTLED, category="shopping"),
        event(
            "e2",
            date(2025, 6, 29),
            "80",
            event_type=EventType.REFUND,
            direction=Direction.CREDIT,
            status=EventStatus.SETTLED,
            category="shopping",
            linked="e1",
        ),
    )
    forecast = build_financial_state(bundle(events))
    assert forecast.starting_state.reserved_total == Decimal("0")


def test_pending_refund_credit_is_excluded():
    events = (
        event("e1", date(2025, 6, 26), "80", status=EventStatus.SETTLED, category="shopping"),
        event(
            "e2",
            date(2025, 7, 20),
            "80",
            event_type=EventType.REFUND,
            direction=Direction.CREDIT,
            status=EventStatus.PENDING,
            category="shopping",
            linked="e1",
        ),
    )
    forecast = build_financial_state(bundle(events))
    assert any(i.reason is ExclusionReason.PENDING_CREDIT for i in forecast.starting_state.excluded_items)


def test_failed_then_retry_counts_only_the_retry():
    events = (
        event(
            "e1",
            date(2025, 8, 10),
            "200",
            event_type=EventType.DEBT_PAYMENT,
            status=EventStatus.SCHEDULED,
            category="utilities",
        ),
        event(
            "e2",
            date(2025, 7, 20),
            "200",
            event_type=EventType.DEBT_PAYMENT,
            status=EventStatus.FAILED,
            category="utilities",
            linked="e1",
        ),
    )
    forecast = build_financial_state(bundle(events))
    confirmed = [e for e in forecast.events if e.kind is CashFlowKind.CONFIRMED_FUTURE]
    assert len(confirmed) == 1
    assert confirmed[0].when == date(2025, 8, 10)


def test_possible_duplicate_is_not_double_counted():
    events = (
        event("e1", date(2025, 7, 5), "90", status=EventStatus.PENDING, category="shopping", linked="e2"),
        event("e2", date(2025, 7, 6), "90", status=EventStatus.SETTLED, category="shopping"),
    )
    forecast = build_financial_state(bundle(events))
    assert forecast.starting_state.reserved_total == Decimal("0")


def test_unrealized_investment_valuation_is_not_spendable_cash():
    events = (
        event(
            "e1",
            date(2025, 7, 1),
            "1000",
            event_type=EventType.INVESTMENT_PURCHASE,
            status=EventStatus.SETTLED,
            category="investment",
        ),
        event(
            "e2",
            date(2025, 7, 20),
            "1500",
            event_type=EventType.INVESTMENT_VALUATION,
            direction=Direction.NON_CASH,
            status=EventStatus.UNREALIZED,
            category="investment",
            linked="e1",
        ),
    )
    forecast = build_financial_state(bundle(events))
    assert forecast.starting_state.available_cash == Decimal("10000")
    assert forecast.starting_state.unrealized_investment_value == Decimal("1500")
    assert all(e.category != "investment" or e.direction is not Direction.CREDIT for e in forecast.events)


def test_realized_sale_is_a_historical_cash_movement():
    events = (
        event(
            "e1",
            date(2025, 7, 1),
            "1000",
            event_type=EventType.INVESTMENT_PURCHASE,
            status=EventStatus.SETTLED,
            category="investment",
        ),
        event(
            "e2",
            date(2025, 7, 20),
            "1200",
            event_type=EventType.INVESTMENT_SALE,
            direction=Direction.CREDIT,
            status=EventStatus.SETTLED,
            category="investment",
            linked="e1",
        ),
    )
    forecast = build_financial_state(bundle(events))
    assert forecast.starting_state.unrealized_investment_value == Decimal("0")
    assert forecast.starting_state.available_cash == Decimal("10000")


def test_monthly_recurrence_projects_on_the_same_day_of_month():
    events = monthly_series("rent", "rent", ["1000", "1000", "1000", "1000", "1000", "1000"])
    forecast = build_financial_state(bundle(events))
    rent_dates = [e.when for e in forecast.events if e.category == "rent"]
    assert rent_dates[:3] == [date(2025, 8, 5), date(2025, 9, 5), date(2025, 10, 5)]


def test_weekly_recurrence_projects_every_seven_days():
    events = tuple(
        event(f"w{i}", date(2025, 7, 4) + timedelta(days=7 * i), "20", category="transport") for i in range(4)
    )
    forecast = build_financial_state(bundle(events))
    transport = sorted(e.when for e in forecast.events if e.category == "transport")
    assert transport[0] == date(2025, 8, 1)
    assert (transport[1] - transport[0]).days == 7


def test_biweekly_recurrence_projects_every_fourteen_days():
    events = tuple(
        event(f"b{i}", date(2025, 6, 6) + timedelta(days=14 * i), "40", category="dining") for i in range(4)
    )
    forecast = build_financial_state(bundle(events))
    dining = sorted(e.when for e in forecast.events if e.category == "dining")
    assert (dining[1] - dining[0]).days == 14


def test_irregular_but_consistent_cadence_uses_mean_interval():
    days = [0, 10, 20, 30, 40]
    events = tuple(
        event(f"i{i}", date(2025, 6, 1) + timedelta(days=d), "30", category="dining") for i, d in enumerate(days)
    )
    forecast = build_financial_state(bundle(events))
    dining = sorted(e.when for e in forecast.events if e.category == "dining")
    assert (dining[1] - dining[0]).days == 10


def test_weak_recurrence_is_labelled_weak_and_can_be_switched_off():
    weak_dates = [date(2025, 3, 1), date(2025, 3, 4), date(2025, 6, 20), date(2025, 7, 25)]
    income_events = tuple(
        event(
            f"wi{i}",
            when,
            "500",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            category="salary",
        )
        for i, when in enumerate(weak_dates)
    )
    expense_events = tuple(
        event(f"we{i}", when, "50", category="shopping") for i, when in enumerate(weak_dates)
    )
    events = income_events + expense_events
    forecast = build_financial_state(bundle(events))
    weak = [e for e in forecast.events if e.certainty is Certainty.PROJECTED_WEAK]
    assert {e.category for e in weak} == {"salary", "shopping"}

    conservative = build_financial_state(
        bundle(events), config=ForecastConfig(project_weak_income=False)
    )
    assert not [e for e in conservative.events if e.category == "salary"]
    assert [e for e in conservative.events if e.category == "shopping"]


def test_weak_income_uses_the_conservative_income_statistic():
    weak_dates = [date(2025, 3, 1), date(2025, 3, 4), date(2025, 6, 20), date(2025, 7, 25)]
    amounts = ["400", "900", "1000", "1100"]
    income_events = tuple(
        event(
            f"wi{i}",
            when,
            amount,
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            category="salary",
        )
        for i, (when, amount) in enumerate(zip(weak_dates, amounts))
    )
    forecast = build_financial_state(bundle(income_events))
    projected = [e for e in forecast.events if e.category == "salary"]
    expected = estimate_amount([Decimal(a) for a in amounts], AmountStatistic.PERCENTILE_25, 3)
    assert projected and projected[0].original_amount == expected
    assert expected < max(Decimal(a) for a in amounts)


def test_one_off_event_is_never_projected():
    events = (event("e1", date(2025, 7, 1), "400", category="medical"),)
    forecast = build_financial_state(bundle(events))
    assert not [e for e in forecast.events if e.category == "medical"]


def test_variable_spending_uses_the_configured_statistic():
    amounts = ["100", "120", "140", "200"]
    events = monthly_series("g", "groceries", amounts)
    forecast = build_financial_state(bundle(events))
    projected = [e for e in forecast.events if e.category == "groceries"]
    expected = estimate_amount([Decimal(a) for a in amounts], AmountStatistic.MEDIAN, 3)
    assert projected[0].original_amount == expected


def test_essential_spending_is_classified_separately_from_flexible():
    essential = monthly_series("g", "groceries", ["100", "110", "120"])
    flexible = monthly_series(
        "s", "streaming", ["30", "30", "30"], flexibility=Flexibility.REDUCIBLE_OR_STOPPABLE, minimum_allowed="15"
    )
    forecast = build_financial_state(bundle(essential + flexible))
    classes = {e.category: e.spending_class for e in forecast.events}
    assert classes["groceries"] is SpendingClass.ESSENTIAL
    assert classes["streaming"] is SpendingClass.FLEXIBLE
    assert forecast.cumulative_future_flexible_expense > 0


def test_flexible_events_expose_reduce_and_stop_permissions():
    flexible = monthly_series(
        "s", "streaming", ["30", "30", "30"], flexibility=Flexibility.REDUCIBLE_OR_STOPPABLE, minimum_allowed="15"
    )
    forecast = build_financial_state(bundle(flexible))
    streaming = [e for e in forecast.events if e.category == "streaming"]
    assert streaming and all(e.is_reducible and e.is_stoppable for e in streaming)


def test_confirmed_future_income_is_projected():
    events = (
        event(
            "e1",
            date(2025, 8, 15),
            "900",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            status=EventStatus.SCHEDULED,
            category="salary",
        ),
    )
    forecast = build_financial_state(bundle(events))
    confirmed = [e for e in forecast.events if e.certainty is Certainty.CONFIRMED]
    assert len(confirmed) == 1
    assert confirmed[0].amount_home == Decimal("900")
    assert forecast.cumulative_future_income == Decimal("900")


def test_speculative_future_income_is_never_counted():
    evidence = evidence_bundle(fact(FactType.INCOME_BONUS_PENDING, amount="5000", effective=date(2025, 8, 20)))
    forecast = build_financial_state(bundle(()), evidence)
    assert forecast.cumulative_future_income == Decimal("0")
    assert any(i.reason is ExclusionReason.SPECULATIVE_INCOME for i in forecast.excluded)


def test_message_only_future_expense_enters_the_forecast():
    evidence = evidence_bundle(
        fact(
            FactType.EXPENSE_NEW_RECURRING,
            amount="200",
            effective=date(2025, 9, 1),
            category="childcare",
            recurrence="monthly",
        )
    )
    forecast = build_financial_state(bundle(()), evidence)
    childcare = [e for e in forecast.events if e.category == "childcare"]
    assert childcare
    assert childcare[0].when == date(2025, 9, 1)
    assert childcare[0].amount_home == Decimal("200")


def test_message_only_expense_with_missing_amount_stays_unresolved():
    evidence = evidence_bundle(
        fact(
            FactType.EXPENSE_NEW_RECURRING,
            amount=None,
            effective=date(2025, 9, 1),
            category="childcare",
            recurrence="monthly",
        )
    )
    forecast = build_financial_state(bundle(()), evidence)
    assert not [e for e in forecast.events if e.category == "childcare"]
    assert [o for o in forecast.unresolved_obligations if o.category == "childcare"]
    assert forecast.cumulative_future_essential_expense == Decimal("0")


def test_future_salary_raise_amends_projected_income():
    salary = monthly_series(
        "sal",
        "salary",
        ["1000", "1000", "1000", "1000", "1000", "1000"],
        event_type=EventType.INCOME,
        direction=Direction.CREDIT,
    )
    evidence = evidence_bundle(
        fact(FactType.SALARY_RAISE, amount="1500", effective=date(2025, 9, 1), category="salary")
    )
    forecast = build_financial_state(bundle(salary), evidence)
    by_date = {e.when: e.amount_home for e in forecast.events if e.category == "salary"}
    assert by_date[date(2025, 8, 5)] == Decimal("1000")
    assert by_date[date(2025, 9, 5)] == Decimal("1500")


def test_income_termination_stops_projected_salary():
    salary = monthly_series(
        "sal",
        "salary",
        ["1000", "1000", "1000", "1000", "1000", "1000"],
        event_type=EventType.INCOME,
        direction=Direction.CREDIT,
    )
    evidence = evidence_bundle(
        fact(FactType.INCOME_TERMINATED, effective=date(2025, 9, 1), category="salary")
    )
    forecast = build_financial_state(bundle(salary), evidence)
    salary_dates = [e.when for e in forecast.events if e.category == "salary"]
    assert salary_dates == [date(2025, 8, 5)]


def test_same_day_ordering_is_credits_first_and_safety_is_ordering_independent():
    events = (
        event(
            "inc",
            date(2025, 8, 10),
            "500",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            status=EventStatus.SCHEDULED,
            category="salary",
        ),
        event("exp", date(2025, 8, 10), "400", status=EventStatus.SCHEDULED, category="rent"),
    )
    forecast = build_financial_state(bundle(events))
    day = next(state for state in forecast.daily_states if state.when == date(2025, 8, 10))
    assert day.applied_event_ids[0].endswith("inc")
    assert day.closing_balance == Decimal("10100")
    assert day.margin_to_minimum == day.closing_balance - Decimal("1000")
    assert not day.breaches_minimum


def test_ninety_day_boundary_is_inclusive_and_bounded():
    horizon_end = REQUEST_DATE + timedelta(days=90)
    events = (
        event("inside", horizon_end, "100", status=EventStatus.SCHEDULED, category="rent"),
        event("outside", horizon_end + timedelta(days=1), "100", status=EventStatus.SCHEDULED, category="rent"),
    )
    forecast = build_financial_state(bundle(events))
    included = {e.source_event_ids[0] for e in forecast.events}
    assert "inside" in included
    assert "outside" not in included
    assert any(i.reason is ExclusionReason.OUTSIDE_WINDOW for i in forecast.excluded)
    assert forecast.daily_states[-1].when == horizon_end
    assert len(forecast.daily_states) == 91


def test_currency_conversion_uses_the_supplied_rate():
    when = date(2025, 8, 20)
    events = (
        event(
            "fx",
            when,
            "100",
            status=EventStatus.SCHEDULED,
            category="rent",
            currency="USD",
        ),
    )
    table = rates((ExchangeRateRecord(when, "USD", HOME, Decimal("16000")),))
    forecast = build_financial_state(bundle(events, rate_table=table))
    converted = [e for e in forecast.events if e.original_currency == "USD"]
    assert converted[0].amount_home == Decimal("1600000.00")
    assert converted[0].conversion_status is ConversionStatus.CONVERTED


def test_missing_exchange_rate_is_never_treated_as_one_to_one():
    events = (
        event("fx", date(2025, 8, 20), "100", status=EventStatus.SCHEDULED, category="rent", currency="USD"),
    )
    forecast = build_financial_state(bundle(events))
    assert not forecast.events
    assert any(i.reason is ExclusionReason.MISSING_EXCHANGE_RATE for i in forecast.excluded)
    assert [o for o in forecast.unresolved_obligations if o.reference_id == "fx"]


def test_minimum_balance_tracking_reports_lowest_point_and_margin():
    events = (event("exp", date(2025, 8, 10), "8500", status=EventStatus.SCHEDULED, category="rent"),)
    forecast = build_financial_state(bundle(events))
    assert forecast.minimum_projected_balance == Decimal("1500")
    assert forecast.minimum_projected_balance_date == date(2025, 8, 10)
    assert forecast.safety_margin == Decimal("500")
    assert not forecast.breaches_minimum


def test_insufficient_future_cash_is_flagged_as_a_deficit():
    events = (event("exp", date(2025, 8, 10), "9500", status=EventStatus.SCHEDULED, category="rent"),)
    forecast = build_financial_state(bundle(events))
    assert forecast.breaches_minimum
    assert forecast.deficit_dates[0] == date(2025, 8, 10)
    assert forecast.safety_margin < 0


def test_unresolved_evidence_never_becomes_a_cash_event():
    evidence = evidence_bundle(
        fact(FactType.UNRESOLVED, status=EvidenceStatus.UNRESOLVED, effective=date(2025, 8, 20))
    )
    forecast = build_financial_state(bundle(()), evidence)
    assert not forecast.events
    assert forecast.unresolved_obligations


def test_untrusted_evidence_is_excluded():
    evidence = evidence_bundle(
        fact(FactType.SALARY_CONFIRMED, amount="9999", effective=date(2025, 8, 20), trusted=False)
    )
    forecast = build_financial_state(bundle(()), evidence)
    assert forecast.cumulative_future_income == Decimal("0")
    assert any(i.reason is ExclusionReason.IRRELEVANT_EVIDENCE for i in forecast.excluded)


def test_simulation_is_deterministic_when_repeated():
    events = monthly_series("g", "groceries", ["100", "120", "140", "160"])
    first = build_financial_state(bundle(events))
    second = build_financial_state(bundle(events))
    assert [(e.event_id, e.when, e.amount_home) for e in first.events] == [
        (e.event_id, e.when, e.amount_home) for e in second.events
    ]
    assert first.minimum_projected_balance == second.minimum_projected_balance
    assert first.daily_states == second.daily_states


def test_monetary_values_stay_decimal_and_quantized():
    events = monthly_series("g", "groceries", ["100.005", "100.015", "100.025"])
    forecast = build_financial_state(bundle(events))
    projected = [e for e in forecast.events if e.category == "groceries"]
    assert all(isinstance(e.amount_home, Decimal) for e in projected)
    assert projected[0].original_amount.as_tuple().exponent >= -2
    assert isinstance(forecast.minimum_projected_balance, Decimal)


def test_no_future_settled_ledger_rows_leak_into_the_forecast():
    events = (
        event("future_settled", date(2025, 8, 20), "700", status=EventStatus.SETTLED, category="rent"),
    )
    forecast = build_financial_state(bundle(events))
    assert not forecast.events
    assert forecast.starting_state.available_cash == Decimal("10000")


def test_pending_debit_is_reserved_against_available_cash():
    events = (event("p1", date(2025, 7, 28), "250", status=EventStatus.PENDING, category="shopping"),)
    forecast = build_financial_state(bundle(events))
    assert forecast.starting_state.reserved_total == Decimal("250")
    assert forecast.starting_state.available_cash == Decimal("9750")


def test_configurable_recurrence_threshold_is_not_hardcoded():
    events = monthly_series("g", "groceries", ["100", "110"])
    strict = build_financial_state(
        bundle(events, recurrence_config=RecurrenceDetectorConfig(minimum_occurrences=3))
    )
    permissive = build_financial_state(
        bundle(events, recurrence_config=RecurrenceDetectorConfig(minimum_occurrences=2))
    )
    assert not strict.events
    assert permissive.events


def test_configurable_horizon_changes_the_window():
    events = monthly_series("g", "groceries", ["100", "110", "120"])
    forecast = build_financial_state(bundle(events), config=ForecastConfig(horizon_days=30))
    assert forecast.window.end_date == REQUEST_DATE + timedelta(days=30)
    assert len(forecast.daily_states) == 31


def test_invariants_reject_an_event_dated_before_the_window():
    events = monthly_series("g", "groceries", ["100", "110", "120"])
    forecast = build_financial_state(bundle(events))
    broken = forecast.__class__(
        **{
            **forecast.__dict__,
            "events": tuple(
                e.__class__(**{**e.__dict__, "when": REQUEST_DATE - timedelta(days=1)}) for e in forecast.events[:1]
            ),
        }
    )
    with pytest.raises(InvariantViolation):
        check_forecast_invariants(broken)


def test_forecast_window_contains_is_inclusive():
    window = ForecastWindow(start_date=REQUEST_DATE, horizon_days=90)
    assert window.contains(REQUEST_DATE)
    assert window.contains(window.end_date)
    assert not window.contains(window.end_date + timedelta(days=1))
