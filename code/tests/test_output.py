from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.decision import RecommendedMethod
from buyorwait.domain import Direction, EventStatus, EventType, Flexibility, PaymentMethod
from buyorwait.explanation import ExplanationError, validate_explanation
from buyorwait.formatting import (
    format_iso_date,
    format_long_date,
    format_money_words,
    format_plan_amount,
    format_safe_amount,
)
from buyorwait.output_record import NONE_TOKEN, OUTPUT_COLUMNS, assemble_output_record
from buyorwait.output_validator import (
    OutputValidationError,
    require_valid_output,
    validate_output_file,
)
from buyorwait.output_writer import write_output_csv
from tests.test_decision import FULL_TODAY, option, scenario
from tests.test_forecast import REQUEST_DATE, event, monthly_series


def record_for(**kwargs):
    bundle, forecast, spec, recommendation = scenario(**kwargs)
    return recommendation, spec, assemble_output_record(recommendation, spec)


def test_output_columns_are_the_exact_contract():
    assert OUTPUT_COLUMNS == (
        "request_id",
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    )


def test_every_field_is_populated_for_an_affordable_now_request():
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    assert record.request_id == "request_x"
    assert record.amount_safe_to_pay == "5000"
    assert record.affordability_status == "affordable_now"
    assert record.recommended_payment_method == "full_payment"
    assert record.payment_plan == "2025-08-01:5000"
    assert record.earliest_date_for_full_payment == "2025-08-01"
    assert record.spending_changes_needed == NONE_TOKEN
    assert record.decision_explanation


def test_row_order_matches_the_column_order():
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    assert record.as_row() == (
        record.request_id,
        record.amount_safe_to_pay,
        record.affordability_status,
        record.recommended_payment_method,
        record.payment_plan,
        record.earliest_date_for_full_payment,
        record.spending_changes_needed,
        record.decision_explanation,
    )


def test_installment_plan_serialization_lists_every_payment():
    installments = option("payment_option_02", PaymentMethod.INSTALLMENTS, "1020", 5, REQUEST_DATE, 10, "100", "5100")
    recommendation, _spec, record = record_for(
        options=(installments,), accepted=(PaymentMethod.INSTALLMENTS,), max_installments=6
    )
    entries = record.payment_plan.split("|")
    assert len(entries) == 5
    assert entries[0] == "2025-08-01:1020"
    assert entries[-1] == "2025-09-10:1020"
    total = sum(Decimal(entry.split(":")[1]) for entry in entries)
    assert total == recommendation.explanation_facts.selected_plan_total_cost


def test_partial_payment_serialization_is_two_payments_summing_to_the_request():
    events = (
        event("cost", REQUEST_DATE + timedelta(days=1), "6500", status=EventStatus.SCHEDULED, category="rent"),
        event(
            "pay",
            REQUEST_DATE + timedelta(days=20),
            "9000",
            event_type=EventType.INCOME,
            direction=Direction.CREDIT,
            status=EventStatus.SCHEDULED,
            category="salary",
        ),
    )
    recommendation, spec, record = record_for(
        events=events,
        options=(FULL_TODAY,),
        accepted=(PaymentMethod.FULL_PAYMENT, PaymentMethod.PARTIAL_PAYMENT),
        allows_partial=True,
    )
    if recommendation.recommended_payment_method is not RecommendedMethod.PARTIAL_PAYMENT:
        pytest.skip("scenario did not select a partial payment")
    first, second = record.payment_plan.split("|")
    assert Decimal(first.split(":")[1]) + Decimal(second.split(":")[1]) == spec.requested_amount_home
    assert first.split(":")[0] == format_iso_date(REQUEST_DATE)


def test_spending_change_serialization_uses_stop_and_reduce_to():
    streaming = monthly_series("s", "streaming", ["900", "900", "900"], flexibility=Flexibility.STOPPABLE)
    recommendation, _spec, record = record_for(
        events=streaming, balance="6500", minimum="1000", requested="5000", options=(FULL_TODAY,)
    )
    assert record.spending_changes_needed.startswith("stop:")
    assert record.spending_changes_needed.split(":")[1] == recommendation.spending_changes[0].event_id

    reducible = monthly_series(
        "r", "streaming", ["600", "600", "600"], flexibility=Flexibility.REDUCIBLE, minimum_allowed="300"
    )
    _rec2, _spec2, record2 = record_for(
        events=reducible,
        balance="7000",
        minimum="1000",
        requested="5000",
        options=(FULL_TODAY,),
        reduce=("streaming",),
        stop=(),
    )
    assert record2.spending_changes_needed.startswith("reduce_to:")
    assert record2.spending_changes_needed.endswith(":300")


def test_not_recommended_produces_none_plan_and_empty_date_when_no_capacity():
    _rec, _spec, record = record_for(balance="2000", minimum="1000", requested="5000", options=(FULL_TODAY,))
    assert record.affordability_status == "not_affordable"
    assert record.recommended_payment_method == "not_recommended"
    assert record.payment_plan == NONE_TOKEN
    assert record.earliest_date_for_full_payment == ""


def test_monetary_formatting_rules():
    assert format_plan_amount(Decimal("620.4")) == "620.40"
    assert format_plan_amount(Decimal("25256")) == "25256"
    assert format_safe_amount(Decimal("603.30")) == "603.3"
    assert format_safe_amount(Decimal("25256.00")) == "25256"
    assert format_money_words(Decimal("15952906.67"), "IDR") == "IDR 15,952,906.67"
    assert format_money_words(Decimal("18000"), "ZAR") == "ZAR 18,000"
    assert format_long_date(date(2025, 8, 8)) == "8 August 2025"


def test_explanation_for_each_status_states_the_binding_constraint():
    _rec, _spec, now_record = record_for(options=(FULL_TODAY,))
    assert "Pay IDR 5,000 today" in now_record.decision_explanation
    assert "1,000" in now_record.decision_explanation

    streaming = monthly_series("s", "streaming", ["900", "900", "900"], flexibility=Flexibility.STOPPABLE)
    _rec2, _spec2, plan_record = record_for(
        events=streaming, balance="6500", minimum="1000", requested="5000", options=(FULL_TODAY,)
    )
    assert plan_record.decision_explanation.startswith("Stop the ")
    assert "then pay IDR 5,000 today" in plan_record.decision_explanation

    _rec3, _spec3, none_record = record_for(balance="2000", minimum="1000", requested="5000", options=(FULL_TODAY,))
    assert none_record.decision_explanation.startswith("Do not make this payment by ")


def test_explanation_is_deterministic():
    first = record_for(options=(FULL_TODAY,))[2].decision_explanation
    second = record_for(options=(FULL_TODAY,))[2].decision_explanation
    assert first == second


def test_explanation_rejects_forbidden_content():
    recommendation, spec, _record = record_for(options=(FULL_TODAY,))
    with pytest.raises(ExplanationError):
        validate_explanation("The model decided this is affordable.", recommendation)
    with pytest.raises(ExplanationError):
        validate_explanation("Event event_12 blocks this.", recommendation)
    with pytest.raises(ExplanationError):
        validate_explanation("", recommendation)


def test_explanation_flags_an_unresolved_obligation():
    FactType = __import__("buyorwait.evidence", fromlist=["FactType"]).FactType
    from tests.test_forecast import evidence_bundle, fact

    evidence = evidence_bundle(
        fact(
            FactType.EXPENSE_NEW_RECURRING,
            amount=None,
            effective=REQUEST_DATE + timedelta(days=10),
            category="childcare",
            recurrence="monthly",
        )
    )
    _rec, _spec, record = record_for(options=(FULL_TODAY,), evidence=evidence)
    assert "no confirmed amount yet" in record.decision_explanation


def test_explanation_never_repeats_an_internal_identifier():
    streaming = monthly_series("s", "streaming", ["900", "900", "900"], flexibility=Flexibility.STOPPABLE)
    _rec, _spec, record = record_for(
        events=streaming, balance="6500", minimum="1000", requested="5000", options=(FULL_TODAY,)
    )
    assert "event_" not in record.decision_explanation
    assert "request_" not in record.decision_explanation


def make_requests(request):
    return {request.request_id: request}


def test_validator_accepts_a_well_formed_file(tmp_path):
    recommendation, spec, record = record_for(options=(FULL_TODAY,))
    path = write_output_csv([record], tmp_path / "output.csv")
    from tests.test_decision import scenario as _scenario

    bundle, _f, _s, _r = _scenario(options=(FULL_TODAY,))
    report = validate_output_file(path, {bundle.request.request_id: bundle.request}, {spec.request_id: recommendation})
    assert report.ok, report.errors
    assert report.rows_checked == 1


def _write_rows(path, rows, header=OUTPUT_COLUMNS):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
    return path


def base_row(record):
    return list(record.as_row())


def test_validator_rejects_a_wrong_header(tmp_path):
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    path = _write_rows(tmp_path / "bad.csv", [base_row(record)], header=OUTPUT_COLUMNS[:-1])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    assert not report.ok
    assert "header mismatch" in report.errors[0]


def test_validator_rejects_duplicate_request_ids(tmp_path):
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    path = _write_rows(tmp_path / "dup.csv", [base_row(record), base_row(record)])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    assert not report.ok
    assert any("duplicate" in error for error in report.errors)


def test_validator_rejects_an_invalid_status(tmp_path):
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    row = base_row(record)
    row[2] = "probably_affordable"
    path = _write_rows(tmp_path / "status.csv", [row])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    assert any("invalid affordability_status" in error for error in report.errors)


def test_validator_rejects_an_invalid_payment_method(tmp_path):
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    row = base_row(record)
    row[3] = "credit_card"
    path = _write_rows(tmp_path / "method.csv", [row])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    assert any("invalid recommended_payment_method" in error for error in report.errors)


def test_validator_rejects_an_invalid_date(tmp_path):
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    row = base_row(record)
    row[5] = "2025-13-45"
    path = _write_rows(tmp_path / "date.csv", [row])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    assert any("invalid earliest_date_for_full_payment" in error for error in report.errors)


def test_validator_rejects_excess_monetary_precision(tmp_path):
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    row = base_row(record)
    row[1] = "4999.999"
    path = _write_rows(tmp_path / "precision.csv", [row])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    assert any("two decimals" in error for error in report.errors)


def test_validator_rejects_a_safe_amount_above_the_request(tmp_path):
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    row = base_row(record)
    row[1] = "99999"
    path = _write_rows(tmp_path / "bounds.csv", [row])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    assert any("outside [0" in error for error in report.errors)


def test_validator_rejects_a_missing_explanation(tmp_path):
    _rec, _spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    row = base_row(record)
    row[7] = ""
    path = _write_rows(tmp_path / "expl.csv", [row])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    assert any("missing decision_explanation" in error for error in report.errors)


def test_validator_rejects_a_row_that_contradicts_the_decision(tmp_path):
    recommendation, spec, record = record_for(options=(FULL_TODAY,))
    bundle = scenario(options=(FULL_TODAY,))[0]
    row = base_row(record)
    row[2] = "affordable_later"
    row[5] = "2025-08-20"
    path = _write_rows(tmp_path / "mismatch.csv", [row])
    report = validate_output_file(
        path, {bundle.request.request_id: bundle.request}, {spec.request_id: recommendation}
    )
    assert any("does not reconcile" in error for error in report.errors)


def test_require_valid_output_raises_loudly(tmp_path):
    bundle = scenario(options=(FULL_TODAY,))[0]
    path = _write_rows(tmp_path / "empty.csv", [])
    report = validate_output_file(path, {bundle.request.request_id: bundle.request})
    with pytest.raises(OutputValidationError):
        require_valid_output(report)
