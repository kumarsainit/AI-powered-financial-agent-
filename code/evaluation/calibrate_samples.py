#!/usr/bin/env python3
"""Development-only calibration: run the decision engine against the 25 public sample requests."""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict

from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from buyorwait import ingestion
from buyorwait.bundle import build_request_bundle
from buyorwait.currency import ExchangeRateTable
from buyorwait.evidence_pipeline import build_evidence_bundle
from buyorwait.forecast import build_financial_state
from buyorwait.ingestion import Dataset
from buyorwait.planning import build_recommendation
from buyorwait.usage import UsageTracker

DATASET_DIR = Path(__file__).parent.parent.parent / "dataset"


def load_sample_dataset(dataset_dir: Path) -> Dataset:
    rows = ingestion._read_rows(dataset_dir / ingestion.SAMPLE_REQUESTS_FILE)
    requests = {}
    for row in rows:
        requests[row["request_id"]] = ingestion._request_from_row(row)
    user_ids = {r.user_id for r in requests.values()}

    profiles = {
        p.user_id: p
        for p in (ingestion._profile_from_row(r) for r in ingestion._read_rows(dataset_dir / ingestion.PROFILES_FILE))
        if p.user_id in user_ids
    }
    events = {
        e.event_id: e
        for e in (ingestion._event_from_row(r) for r in ingestion._read_rows(dataset_dir / ingestion.EVENTS_FILE))
        if e.user_id in user_ids
    }
    events_by_user = defaultdict(list)
    for event in events.values():
        events_by_user[event.user_id].append(event)

    options = defaultdict(list)
    for row in ingestion._read_rows(dataset_dir / ingestion.PAYMENT_OPTIONS_FILE):
        option = ingestion._payment_option_from_row(row)
        if option.request_id in requests:
            options[option.request_id].append(option)

    messages_by_user = defaultdict(list)
    messages_by_request = defaultdict(list)
    for row in ingestion._read_rows(dataset_dir / ingestion.MESSAGES_FILE):
        message = ingestion._message_from_row(row)
        if message.user_id not in user_ids:
            continue
        messages_by_user[message.user_id].append(message)
        if message.request_id is not None:
            messages_by_request[message.request_id].append(message)

    images_by_request = defaultdict(list)
    for row in ingestion._read_rows(dataset_dir / ingestion.IMAGES_FILE):
        image = ingestion._image_from_row(row, dataset_dir)
        if image.user_id in user_ids and image.request_id is not None:
            images_by_request[image.request_id].append(image)

    rates = [ingestion._exchange_rate_from_row(r) for r in ingestion._read_rows(dataset_dir / ingestion.EXCHANGE_RATES_FILE)]

    return Dataset(
        profiles=profiles,
        requests=requests,
        events=events,
        events_by_user={u: tuple(sorted(v, key=lambda e: e.event_date)) for u, v in events_by_user.items()},
        payment_options_by_request={k: tuple(v) for k, v in options.items()},
        messages_by_user={k: tuple(v) for k, v in messages_by_user.items()},
        messages_by_request={k: tuple(v) for k, v in messages_by_request.items()},
        images_by_request={k: tuple(v) for k, v in images_by_request.items()},
        exchange_rates=ExchangeRateTable.from_records(rates),
    )


def main() -> int:
    truth = {r["request_id"]: r for r in csv.DictReader(open(DATASET_DIR / "sample_requests.csv"))}
    dataset = load_sample_dataset(DATASET_DIR)
    tracker = UsageTracker()

    status_match = 0
    method_match = 0
    earliest_match = 0
    rows = []
    for request_id in sorted(truth, key=lambda r: int(r.split("_")[1])):
        bundle = build_request_bundle(dataset, request_id)
        forecast = build_financial_state(bundle, build_evidence_bundle(bundle, tracker))
        recommendation = build_recommendation(bundle, forecast)

        expected = truth[request_id]
        got_status = recommendation.affordability_status.value
        got_method = recommendation.recommended_payment_method.value
        got_earliest = (
            recommendation.earliest_date_for_full_payment.isoformat()
            if recommendation.earliest_date_for_full_payment
            else ""
        )
        status_match += got_status == expected["affordability_status"]
        method_match += got_method == expected["recommended_payment_method"]
        earliest_match += got_earliest == expected["earliest_date_for_full_payment"]
        rows.append(
            (
                request_id,
                expected["affordability_status"],
                got_status,
                expected["recommended_payment_method"],
                got_method,
                expected["amount_safe_to_pay"],
                str(recommendation.amount_safe_to_pay),
                expected["earliest_date_for_full_payment"],
                got_earliest,
            )
        )

    total = len(rows)
    print(f"sample requests: {total}")
    print(f"affordability_status match: {status_match}/{total}")
    print(f"recommended_payment_method match: {method_match}/{total}")
    print(f"earliest_date_for_full_payment match: {earliest_match}/{total}")
    print()
    print("request | truth_status | got_status | truth_method | got_method | truth_safe | got_safe | truth_date | got_date")
    for row in rows:
        flag = "" if row[1] == row[2] and row[3] == row[4] else "  <-- differs"
        print(" | ".join(row) + flag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
