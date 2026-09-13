from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.ingestion import load_sample_request_ids, load_sample_request_user_ids
from buyorwait.output_record import OUTPUT_COLUMNS
from buyorwait.output_validator import require_valid_output, validate_output_file
from buyorwait.pipeline import run_pipeline
from tests.paths import DATASET_DIR

EXPECTED_ROWS = 250


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    output = tmp_path_factory.mktemp("run") / "output.csv"
    return run_pipeline(DATASET_DIR, output)


def read_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def test_pipeline_produces_one_row_per_production_request(pipeline):
    rows = read_rows(pipeline.output_path)
    assert tuple(rows[0]) == OUTPUT_COLUMNS
    assert len(rows) - 1 == EXPECTED_ROWS
    assert len(pipeline.results) == EXPECTED_ROWS


def test_pipeline_output_passes_strict_validation(pipeline):
    report = validate_output_file(
        pipeline.output_path,
        pipeline.dataset.requests,
        pipeline.recommendations,
        load_sample_request_user_ids(DATASET_DIR),
    )
    require_valid_output(report)
    assert report.rows_checked == EXPECTED_ROWS


def test_pipeline_contains_no_sample_requests_or_users(pipeline):
    sample_requests = load_sample_request_ids(DATASET_DIR)
    sample_users = load_sample_request_user_ids(DATASET_DIR)
    produced_ids = {result.request_id for result in pipeline.results}
    assert not (produced_ids & sample_requests)
    assert not ({result.spec.user_id for result in pipeline.results} & sample_users)


def test_pipeline_request_ids_are_unique_and_complete(pipeline):
    produced = [result.request_id for result in pipeline.results]
    assert len(produced) == len(set(produced))
    assert set(produced) == set(pipeline.dataset.requests)


def test_pipeline_rows_reconcile_with_the_decision_results(pipeline):
    rows = {row[0]: row for row in read_rows(pipeline.output_path)[1:]}
    for result in pipeline.results:
        row = rows[result.request_id]
        assert row[2] == result.recommendation.affordability_status.value
        assert row[3] == result.recommendation.recommended_payment_method.value
        assert Decimal(row[1]) == result.recommendation.amount_safe_to_pay
        assert row[7] == result.record.decision_explanation


def test_pipeline_is_deterministic(tmp_path):
    first = run_pipeline(DATASET_DIR, tmp_path / "first.csv")
    second = run_pipeline(DATASET_DIR, tmp_path / "second.csv")
    assert read_rows(first.output_path) == read_rows(second.output_path)
    assert [r.record for r in first.results] == [r.record for r in second.results]
    assert [r.recommendation.payment_plan for r in first.results] == [
        r.recommendation.payment_plan for r in second.results
    ]
    assert (tmp_path / "first.csv").read_bytes() == (tmp_path / "second.csv").read_bytes()


def test_pipeline_refuses_to_run_when_sample_users_leak(monkeypatch, tmp_path):
    from buyorwait import pipeline as pipeline_module

    real_loader = pipeline_module.load_sample_request_user_ids

    def leaky(dataset_dir):
        dataset = pipeline_module.load_dataset(dataset_dir)
        return frozenset({next(iter(dataset.requests.values())).user_id}) | real_loader(dataset_dir)

    monkeypatch.setattr(pipeline_module, "load_sample_request_user_ids", leaky)
    with pytest.raises(ValueError):
        run_pipeline(DATASET_DIR, tmp_path / "leak.csv")


def test_every_explanation_is_non_empty_and_single_line(pipeline):
    for result in pipeline.results:
        text = result.record.decision_explanation
        assert text.strip()
        assert "\n" not in text
        assert len(text) <= 400
