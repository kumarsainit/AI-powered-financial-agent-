#!/usr/bin/env python3
"""Deterministic backtest of candidate amount estimators for Phase 4 forecasting."""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import mean

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from buyorwait.bundle import build_request_bundle
from buyorwait.domain import Direction, RecurrenceStrength
from buyorwait.estimators import AmountStatistic, estimate_amount
from buyorwait.ingestion import load_dataset
from buyorwait.projection import classify_spending


DATASET_DIR = Path(__file__).parent.parent.parent / "dataset"
MINIMUM_TRAIN_OBSERVATIONS = 3
RECENT_WINDOW = 3
CANDIDATES = tuple(AmountStatistic)


def collect_series(dataset):
    series: dict[tuple[str, str], dict] = {}
    for request_id in sorted(dataset.requests):
        bundle = build_request_bundle(dataset, request_id)
        events_by_id = {e.event_id: e for e in bundle.events if e.event_id in bundle.included_event_ids}
        for candidate in bundle.recurrence_candidates:
            if candidate.strength is RecurrenceStrength.ONE_OFF:
                continue
            events = [events_by_id[i] for i in candidate.source_event_ids if i in events_by_id]
            events = [e for e in events if e.amount is not None]
            if len(events) < MINIMUM_TRAIN_OBSERVATIONS + 1:
                continue
            directions = {e.direction for e in events}
            if len(directions) != 1:
                continue
            (direction,) = directions
            if direction is Direction.NON_CASH:
                continue
            spending_class = classify_spending(events, bundle.profile, direction)
            key = (candidate.user_id, candidate.category)
            series[key] = {
                "amounts": [e.amount for e in sorted(events, key=lambda e: e.event_date)],
                "class": spending_class,
                "strength": candidate.strength,
            }
    return series


def backtest_cumulative(series, horizon_occurrences: int = 6):
    results: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"abs": [], "signed": [], "under": 0, "over": 0, "n": 0, "worst": Decimal("0")}
    )
    for (_user, _category), record in sorted(series.items()):
        amounts = record["amounts"]
        class_name = record["class"].value
        for cutoff in range(MINIMUM_TRAIN_OBSERVATIONS, len(amounts)):
            history = amounts[:cutoff]
            future = amounts[cutoff : cutoff + horizon_occurrences]
            if not future:
                continue
            actual_total = sum(future, start=Decimal("0"))
            if actual_total == 0:
                continue
            for statistic in CANDIDATES:
                predicted_total = estimate_amount(history, statistic, RECENT_WINDOW) * Decimal(len(future))
                relative = (predicted_total - actual_total) / actual_total
                bucket = results[(class_name, statistic.value)]
                bucket["n"] += 1
                bucket["abs"].append(abs(relative))
                bucket["signed"].append(relative)
                if relative < 0:
                    bucket["under"] += 1
                else:
                    bucket["over"] += 1
                if abs(relative) > bucket["worst"]:
                    bucket["worst"] = abs(relative)
    return results


def backtest(series):
    results: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"abs": [], "signed": [], "under": 0, "over": 0, "n": 0, "worst": Decimal("0")}
    )
    for (_user, _category), record in sorted(series.items()):
        amounts = record["amounts"]
        class_name = record["class"].value
        for cutoff in range(MINIMUM_TRAIN_OBSERVATIONS, len(amounts)):
            history = amounts[:cutoff]
            actual = amounts[cutoff]
            if actual == 0:
                continue
            for statistic in CANDIDATES:
                predicted = estimate_amount(history, statistic, RECENT_WINDOW)
                relative = (predicted - actual) / actual
                bucket = results[(class_name, statistic.value)]
                bucket["n"] += 1
                bucket["abs"].append(abs(relative))
                bucket["signed"].append(relative)
                if relative < 0:
                    bucket["under"] += 1
                else:
                    bucket["over"] += 1
                if abs(relative) > bucket["worst"]:
                    bucket["worst"] = abs(relative)
    return results


def format_report(results) -> str:
    lines = [
        "| Series class | Statistic | n | Mean abs err | Mean signed err | Under-estimation rate | Over-estimation rate | Worst abs err |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (class_name, statistic), bucket in sorted(results.items()):
        if not bucket["n"]:
            continue
        lines.append(
            "| {cls} | `{stat}` | {n} | {mae:.4f} | {signed:+.4f} | {under:.1%} | {over:.1%} | {worst:.4f} |".format(
                cls=class_name,
                stat=statistic,
                n=bucket["n"],
                mae=float(mean(bucket["abs"])),
                signed=float(mean(bucket["signed"])),
                under=bucket["under"] / bucket["n"],
                over=bucket["over"] / bucket["n"],
                worst=float(bucket["worst"]),
            )
        )
    return "\n".join(lines)


def main() -> int:
    dataset = load_dataset(DATASET_DIR)
    series = collect_series(dataset)
    print(f"series backtested: {len(series)}")
    print("\n### Per-occurrence backtest\n")
    print(format_report(backtest(series)))
    print("\n### Cumulative 6-occurrence backtest\n")
    print(format_report(backtest_cumulative(series)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
