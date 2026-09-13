#!/usr/bin/env python3
"""Phase 6 production validation: output assembly, explanations, serialization and determinism."""
from __future__ import annotations

import csv
import hashlib
import os
import statistics
import sys
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from buyorwait.decision import CandidateKind
from buyorwait.ingestion import load_sample_request_ids, load_sample_request_user_ids
from buyorwait.output_record import NONE_TOKEN, OUTPUT_COLUMNS
from buyorwait.output_validator import validate_output_file
from buyorwait.pipeline import run_pipeline

REPO_ROOT = Path(__file__).parent.parent.parent
DATASET_DIR = REPO_ROOT / "dataset"
OUTPUT_PATH = REPO_ROOT / "output.csv"


def summarize(values):
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": ordered[len(ordered) // 2],
        "max": ordered[-1],
        "mean": round(statistics.mean(float(v) for v in ordered), 2),
    }


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main() -> int:
    result = run_pipeline(DATASET_DIR, OUTPUT_PATH)
    sample_requests = load_sample_request_ids(DATASET_DIR)
    sample_users = load_sample_request_user_ids(DATASET_DIR)

    report = validate_output_file(
        OUTPUT_PATH, result.dataset.requests, result.recommendations, sample_users
    )

    with OUTPUT_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    statuses = Counter(row["affordability_status"] for row in rows)
    methods = Counter(row["recommended_payment_method"] for row in rows)
    safe_amounts = [Decimal(row["amount_safe_to_pay"]) for row in rows]
    plan_lengths = Counter(
        0 if row["payment_plan"] == NONE_TOKEN else len(row["payment_plan"].split("|")) for row in rows
    )
    change_counts = Counter(
        0 if row["spending_changes_needed"] == NONE_TOKEN else len(row["spending_changes_needed"].split("|"))
        for row in rows
    )
    change_actions = Counter(
        part.split(":")[0]
        for row in rows
        if row["spending_changes_needed"] != NONE_TOKEN
        for part in row["spending_changes_needed"].split("|")
    )
    empty_dates = sum(1 for row in rows if not row["earliest_date_for_full_payment"])
    explanation_lengths = [len(row["decision_explanation"]) for row in rows]
    unresolved = sum(
        1 for result_item in result.results if result_item.recommendation.explanation_facts.blocking_obligations
    )
    installment_selected = sum(
        1
        for item in result.results
        for evaluation in item.recommendation.candidates
        if evaluation.candidate.candidate_id == item.recommendation.selected_candidate_id
        and evaluation.candidate.kind is CandidateKind.INSTALLMENTS
    )
    fee_total = sum(
        (
            item.recommendation.explanation_facts.selected_plan_financing_fee or Decimal("0")
            for item in result.results
        ),
        start=Decimal("0"),
    )

    with tempfile.TemporaryDirectory() as tmp:
        repeat_path = Path(tmp) / "output.csv"
        repeat = run_pipeline(DATASET_DIR, repeat_path)
        identical_bytes = digest(OUTPUT_PATH) == digest(repeat_path)
        identical_records = [r.record for r in result.results] == [r.record for r in repeat.results]

    print(f"output path: {OUTPUT_PATH}")
    print(f"schema: {OUTPUT_COLUMNS}")
    print(f"rows generated: {len(rows)}")
    print(f"validation errors: {len(report.errors)} {report.errors[:5]}")
    print(f"rows checked by the validator: {report.rows_checked}")
    print(f"unique request ids: {len({row['request_id'] for row in rows})}")
    print(f"sample request ids present: {len({row['request_id'] for row in rows} & sample_requests)}")
    print(f"sample users present: {len({r.spec.user_id for r in result.results} & sample_users)}")
    print(f"affordability status: {dict(statuses)}")
    print(f"recommended method: {dict(methods)}")
    print(f"amount_safe_to_pay: {summarize(safe_amounts)}")
    print(f"safe amount equals the requested amount: {sum(1 for row, amount in zip(rows, safe_amounts) if amount == result.dataset.requests[row['request_id']].requested_amount)}")
    print(f"safe amount zero: {sum(1 for amount in safe_amounts if amount == 0)}")
    print(f"payment plan lengths: {dict(sorted(plan_lengths.items()))}")
    print(f"installment plans selected: {installment_selected}")
    print(f"total financing fees in selected plans: {fee_total}")
    print(f"spending change counts: {dict(sorted(change_counts.items()))}")
    print(f"spending change actions: {dict(change_actions)}")
    print(f"rows with an empty earliest_date_for_full_payment: {empty_dates}")
    print(f"explanation length: {summarize(explanation_lengths)}")
    print(f"requests flagged with unresolved obligations: {unresolved}")
    print(f"deterministic byte-identical rerun: {identical_bytes}")
    print(f"deterministic identical records: {identical_records}")
    print(f"output sha256: {digest(OUTPUT_PATH)}")
    return 0 if report.ok and identical_bytes and identical_records else 1


if __name__ == "__main__":
    raise SystemExit(main())
