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
from buyorwait.output_record import assemble_output_record
from buyorwait.planning import build_recommendation, normalize_request
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


FIELDS = (
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)


def main() -> int:
    truth = {r["request_id"]: r for r in csv.DictReader(open(DATASET_DIR / "sample_requests.csv"))}
    dataset = load_sample_dataset(DATASET_DIR)
    tracker = UsageTracker()

    matches = {field: 0 for field in FIELDS}
    rows = []
    for request_id in sorted(truth, key=lambda r: int(r.split("_")[1])):
        bundle = build_request_bundle(dataset, request_id)
        forecast = build_financial_state(bundle, build_evidence_bundle(bundle, tracker))
        spec = normalize_request(bundle, forecast)
        recommendation = build_recommendation(bundle, forecast, spec=spec)
        record = assemble_output_record(recommendation, spec)

        expected = truth[request_id]
        produced = {
            "amount_safe_to_pay": record.amount_safe_to_pay,
            "affordability_status": record.affordability_status,
            "recommended_payment_method": record.recommended_payment_method,
            "payment_plan": record.payment_plan,
            "earliest_date_for_full_payment": record.earliest_date_for_full_payment,
            "spending_changes_needed": record.spending_changes_needed,
        }
        for field in FIELDS:
            if produced[field] == expected[field]:
                matches[field] += 1
        rows.append((request_id, expected, produced, record.decision_explanation))

    total = len(rows)
    print(f"sample requests: {total}")
    for field in FIELDS:
        print(f"{field}: {matches[field]}/{total}")
    print()
    for request_id, expected, produced, explanation in rows:
        differing = [f for f in FIELDS if produced[f] != expected[f]]
        marker = "OK  " if not differing else "DIFF"
        print(f"{marker} {request_id} status {expected['affordability_status']} -> {produced['affordability_status']}")
        for field in differing:
            print(f"      {field}: truth={expected[field]!r} got={produced[field]!r}")
        if not differing:
            print(f"      truth expl: {expected['decision_explanation']}")
            print(f"      our   expl: {explanation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
