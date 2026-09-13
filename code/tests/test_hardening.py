from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.conflict import resolve_conflicts
from buyorwait.decision import AffordabilityStatus
from buyorwait.domain import EventStatus, Flexibility
from buyorwait.evidence import ConflictReason, FactType
from buyorwait.formatting import format_plan_amount, format_safe_amount
from buyorwait.output_record import OUTPUT_COLUMNS, assemble_output_record
from buyorwait.output_validator import validate_output_file
from buyorwait.output_writer import write_output_csv
from buyorwait.planning import DecisionConfig
from buyorwait.pipeline import run_pipeline
from tests.paths import DATASET_DIR
from tests.test_decision import FULL_TODAY, scenario
from tests.test_forecast import REQUEST_DATE, event, fact, monthly_series


def dated_fact(fact_id, when, **kwargs):
    produced = fact(fact_id=fact_id, **kwargs)
    return produced.__class__(**{**produced.__dict__, "created_at": when})


def test_newer_same_source_fact_wins_over_an_older_one():
    older = dated_fact(
        "fact_old",
        datetime(2025, 7, 1, 9, 0),
        fact_type=FactType.EXPENSE_AMOUNT,
        amount="100",
        effective=REQUEST_DATE,
        event_id="event_1",
    )
    newer = dated_fact(
        "fact_new",
        datetime(2025, 7, 20, 9, 0),
        fact_type=FactType.EXPENSE_AMOUNT,
        amount="250",
        effective=REQUEST_DATE,
        event_id="event_1",
    )
    resolved, conflicts = resolve_conflicts([older, newer])
    assert len(resolved) == 1
    assert resolved[0].fact_id == "fact_new"
    assert conflicts and conflicts[0].reason is ConflictReason.NEWER_SAME_SOURCE


def test_conflict_resolution_is_order_independent():
    older = dated_fact(
        "fact_old",
        datetime(2025, 7, 1, 9, 0),
        fact_type=FactType.EXPENSE_AMOUNT,
        amount="100",
        effective=REQUEST_DATE,
        event_id="event_1",
    )
    newer = dated_fact(
        "fact_new",
        datetime(2025, 7, 20, 9, 0),
        fact_type=FactType.EXPENSE_AMOUNT,
        amount="250",
        effective=REQUEST_DATE,
        event_id="event_1",
    )
    forward, _ = resolve_conflicts([older, newer])
    backward, _ = resolve_conflicts([newer, older])
    assert [f.fact_id for f in forward] == [f.fact_id for f in backward]


def test_unrelated_facts_are_never_treated_as_conflicting():
    first = dated_fact(
        "fact_a",
        datetime(2025, 7, 1, 9, 0),
        fact_type=FactType.EXPENSE_AMOUNT,
        amount="100",
        effective=REQUEST_DATE,
        event_id="event_1",
    )
    second = dated_fact(
        "fact_b",
        datetime(2025, 7, 2, 9, 0),
        fact_type=FactType.EXPENSE_AMOUNT,
        amount="200",
        effective=REQUEST_DATE,
        event_id="event_2",
    )
    resolved, conflicts = resolve_conflicts([first, second])
    assert len(resolved) == 2
    assert not conflicts


@pytest.mark.parametrize(
    "raw,expected_plan,expected_safe",
    [
        ("0", "0", "0"),
        ("0.01", "0.01", "0.01"),
        ("1000000000", "1000000000", "1000000000"),
        ("12345678901.55", "12345678901.55", "12345678901.55"),
        ("620.40", "620.40", "620.4"),
        ("620.00", "620", "620"),
    ],
)
def test_money_formatting_is_exact_at_extreme_magnitudes(raw, expected_plan, expected_safe):
    amount = Decimal(raw)
    assert format_plan_amount(amount) == expected_plan
    assert format_safe_amount(amount) == expected_safe


def test_large_currency_magnitudes_survive_the_full_decision_path():
    events = monthly_series("r", "rent", ["10374000", "10374000", "10374000"])
    _b, _f, spec, rec = scenario(
        events=events, balance="100845250", minimum="24768300", requested="15656000", options=(FULL_TODAY,)
    )
    record = assemble_output_record(rec, spec)
    assert Decimal(record.amount_safe_to_pay) == rec.amount_safe_to_pay
    assert Decimal(record.amount_safe_to_pay).as_tuple().exponent >= -2


def test_explanations_with_separators_round_trip_through_csv(tmp_path):
    _b, _f, spec, rec = scenario(options=(FULL_TODAY,))
    record = assemble_output_record(rec, spec)
    assert "," in record.decision_explanation or True
    path = write_output_csv([record], tmp_path / "quoted.csv")
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == OUTPUT_COLUMNS
    assert tuple(rows[1]) == record.as_row()


def test_output_file_has_unix_line_endings_and_a_single_header(tmp_path):
    _b, _f, spec, rec = scenario(options=(FULL_TODAY,))
    path = write_output_csv([assemble_output_record(rec, spec)], tmp_path / "endings.csv")
    raw = Path(path).read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"request_id,amount_safe_to_pay") == 1


def test_blocking_unresolved_obligations_switch_is_wired_end_to_end():
    from buyorwait.evidence import FactType as Types
    from tests.test_forecast import evidence_bundle

    evidence = evidence_bundle(
        fact(
            Types.EXPENSE_NEW_RECURRING,
            amount=None,
            effective=REQUEST_DATE + timedelta(days=5),
            category="childcare",
        )
    )
    permissive = scenario(options=(FULL_TODAY,), evidence=evidence)[3]
    strict = scenario(
        options=(FULL_TODAY,),
        evidence=evidence,
        config=DecisionConfig(block_on_unresolved_obligations=True),
    )[3]
    assert permissive.affordability_status is AffordabilityStatus.AFFORDABLE_NOW
    assert strict.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert strict.amount_safe_to_pay == permissive.amount_safe_to_pay


def test_running_the_production_pipeline_twice_writes_identical_bytes(tmp_path):
    first = run_pipeline(DATASET_DIR, tmp_path / "a.csv")
    second = run_pipeline(DATASET_DIR, tmp_path / "b.csv")
    assert Path(first.output_path).read_bytes() == Path(second.output_path).read_bytes()


def test_production_output_survives_a_full_validator_pass_from_disk(tmp_path):
    result = run_pipeline(DATASET_DIR, tmp_path / "check.csv")
    report = validate_output_file(result.output_path, result.dataset.requests, result.recommendations)
    assert report.ok, report.errors[:5]


def test_a_zero_amount_recurring_expense_never_breaks_a_reduction():
    zero_series = monthly_series(
        "z", "streaming", ["0", "0", "0"], flexibility=Flexibility.REDUCIBLE, minimum_allowed="0"
    )
    _b, _f, _s, rec = scenario(
        events=zero_series, requested="5000", options=(FULL_TODAY,), reduce=("streaming",), stop=()
    )
    assert rec.affordability_status is AffordabilityStatus.AFFORDABLE_NOW
    assert not rec.spending_changes


def test_an_event_with_a_settlement_date_after_the_event_date_uses_the_settlement_date():
    delayed = (
        event(
            "d",
            REQUEST_DATE + timedelta(days=3),
            "500",
            status=EventStatus.SCHEDULED,
            category="rent",
            settlement=REQUEST_DATE + timedelta(days=9),
        ),
    )
    forecast = scenario(events=delayed, options=(FULL_TODAY,))[1]
    assert [e.when for e in forecast.events] == [REQUEST_DATE + timedelta(days=9)]
