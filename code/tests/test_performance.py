from __future__ import annotations

import csv
import random
import shutil
import sys
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait import simulator
from buyorwait.domain import Direction
from buyorwait.output_writer import write_output_csv
from buyorwait.pipeline import process_request, run_pipeline, sorted_request_ids
from buyorwait.forecast_config import ForecastConfig
from buyorwait.planning import DecisionConfig
from buyorwait.usage import UsageTracker
from tests.paths import DATASET_DIR


def _reference_simulate(opening_balance, events, window, minimum_balance):
    by_day = defaultdict(list)
    for event in events:
        if event.amount_home is None:
            continue
        if not window.contains(event.when):
            continue
        by_day[event.when].append(event)

    balance = opening_balance
    states = []
    for day_index in range(window.horizon_days + 1):
        current = window.start_date + timedelta(days=day_index)
        day_events = sorted(by_day.get(current, ()), key=simulator.ordering_key)
        low = balance
        for event in day_events:
            amount = event.amount_home or Decimal("0")
            if event.direction is Direction.CREDIT:
                balance += amount
            elif event.direction is Direction.DEBIT:
                balance -= amount
            if balance < low:
                low = balance
    return balance


@pytest.fixture(scope="module")
def dataset_pipeline():
    return run_pipeline(DATASET_DIR, None)


def test_repeated_full_runs_are_byte_identical(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    run_pipeline(DATASET_DIR, first)
    run_pipeline(DATASET_DIR, second)
    assert first.read_bytes() == second.read_bytes()


def test_rerun_over_an_existing_output_file_is_clean(tmp_path):
    output = tmp_path / "output.csv"
    output.write_text("stale content that must be fully replaced\n", encoding="utf-8")
    run_pipeline(DATASET_DIR, output)
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 251
    assert "stale content" not in output.read_text(encoding="utf-8")


def test_shuffled_financial_events_produce_identical_recommendations(tmp_path, dataset_pipeline):
    shuffled_dir = tmp_path / "shuffled_dataset"
    shuffled_dir.mkdir(parents=True)
    for name in (
        "requests.csv",
        "financial_profiles.csv",
        "exchange_rates.csv",
        "request_payment_options.csv",
        "messages.csv",
        "images.csv",
        "sample_requests.csv",
    ):
        shutil.copy(DATASET_DIR / name, shuffled_dir / name)
    shutil.copytree(DATASET_DIR / "media", shuffled_dir / "media")

    with (DATASET_DIR / "financial_events.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = list(reader)

    shuffled_rows = rows[:]
    random.Random(1337).shuffle(shuffled_rows)
    with (shuffled_dir / "financial_events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(shuffled_rows)

    shuffled_result = run_pipeline(shuffled_dir, None)

    original_rows = {r.request_id: r.record.as_row() for r in dataset_pipeline.results}
    shuffled_rows_by_id = {r.request_id: r.record.as_row() for r in shuffled_result.results}
    assert set(original_rows) == set(shuffled_rows_by_id)
    for request_id, row in original_rows.items():
        assert shuffled_rows_by_id[request_id] == row


def test_no_model_calls_occur_without_an_api_key(monkeypatch, dataset_pipeline):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = run_pipeline(DATASET_DIR, None)
    assert result.tracker.total_calls() == 0
    assert dataset_pipeline.tracker.total_calls() == 0


def test_simulation_counts_stay_within_a_measured_sane_bound():
    from buyorwait.ingestion import load_dataset

    dataset = load_dataset(DATASET_DIR)
    tracker = UsageTracker()
    call_counts = []

    import buyorwait.planning as planning_module

    for request_id in sorted_request_ids(dataset):
        count_holder = [0]
        real = planning_module.simulate

        def wrapped(*args, count_holder=count_holder, real=real, **kwargs):
            count_holder[0] += 1
            return real(*args, **kwargs)

        planning_module.simulate = wrapped
        try:
            process_request(dataset, request_id, tracker, ForecastConfig(), DecisionConfig())
        finally:
            planning_module.simulate = real
        call_counts.append(count_holder[0])

    assert max(call_counts) < 100
    assert sum(call_counts) / len(call_counts) < 50


def test_simulate_matches_the_naive_per_day_sort_reference(dataset_pipeline):
    import buyorwait.planning as planning_module
    from buyorwait.ingestion import load_dataset

    captured = []
    real_simulate = simulator.simulate

    def capturing(opening_balance, events, window, minimum_balance):
        events = tuple(events)
        captured.append((opening_balance, events, window, minimum_balance))
        return real_simulate(opening_balance, events, window, minimum_balance)

    planning_module.simulate = capturing
    try:
        dataset = load_dataset(DATASET_DIR)
        tracker = UsageTracker()
        for request_id in sorted_request_ids(dataset)[:40]:
            process_request(dataset, request_id, tracker, ForecastConfig(), DecisionConfig())
    finally:
        planning_module.simulate = real_simulate

    assert len(captured) > 0
    for opening_balance, events, window, minimum_balance in captured:
        optimized_states = real_simulate(opening_balance, events, window, minimum_balance)
        reference_final_balance = _reference_simulate(opening_balance, events, window, minimum_balance)
        assert optimized_states[-1].closing_balance == reference_final_balance


def test_output_writer_leaves_no_partial_or_temp_file_on_failure(tmp_path):
    target = tmp_path / "output.csv"
    target.write_text("previous good content\n", encoding="utf-8")

    def failing_records():
        raise RuntimeError("boom")
        yield None

    with pytest.raises(RuntimeError):
        write_output_csv(failing_records(), target)

    assert target.read_text(encoding="utf-8") == "previous good content\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_output_writer_replaces_atomically(tmp_path):
    target = tmp_path / "output.csv"
    from buyorwait.output_record import OUTPUT_COLUMNS

    write_output_csv([], target)
    with target.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows == [list(OUTPUT_COLUMNS)]
    assert list(tmp_path.glob("*.tmp")) == []
