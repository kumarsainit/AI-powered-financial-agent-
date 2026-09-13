#!/usr/bin/env python3
"""Phase 3 production evidence validation across all 250 requests."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collections import Counter, defaultdict
from pathlib import Path

from buyorwait.bundle import build_request_bundle
from buyorwait.evidence_pipeline import build_evidence_bundle
from buyorwait.evidence import EvidenceStatus, ExtractionMethod, FactType
from buyorwait.ingestion import load_dataset
from buyorwait.usage import UsageTracker

DATASET_DIR = Path(__file__).parent.parent.parent / "dataset"


def run_validation():
    dataset = load_dataset(DATASET_DIR)
    tracker = UsageTracker()

    total_requests = len(dataset.requests)
    total_messages = 0
    total_images = 0
    deterministic_count = 0
    ai_text_count = 0
    ai_vlm_count = 0
    unresolved_count = 0
    conflict_count = 0
    untrusted_count = 0

    fact_type_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    source_type_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()

    examples_message_only: list[str] = []
    examples_unresolved: list[str] = []
    examples_ambiguous: list[str] = []
    examples_conflicts: list[str] = []

    errors = []

    for request_id in sorted(dataset.requests.keys()):
        try:
            bundle = build_request_bundle(dataset, request_id)
            eb = build_evidence_bundle(bundle, tracker)

            total_messages += len(bundle.messages)
            total_images += len(bundle.images)
            conflict_count += len(eb.conflicts)
            unresolved_count += eb.unresolved_count
            if eb.has_untrusted_content:
                untrusted_count += 1

            for fact in eb.facts:
                fact_type_counts[fact.fact_type.value] += 1
                status_counts[fact.status.value] += 1
                source_type_counts[fact.source_type.value] += 1
                method_counts[fact.extraction_method.value] += 1
                if fact.extraction_method == ExtractionMethod.DETERMINISTIC:
                    deterministic_count += 1
                elif fact.extraction_method == ExtractionMethod.AI_TEXT:
                    ai_text_count += 1
                elif fact.extraction_method == ExtractionMethod.AI_VLM:
                    ai_vlm_count += 1

                if fact.status == EvidenceStatus.UNRESOLVED and len(examples_unresolved) < 5:
                    examples_unresolved.append(
                        f"  {request_id} / {fact.source_id}: {fact.fact_type.value} — {fact.description[:80]}"
                    )
                if fact.ambiguity_note and len(examples_ambiguous) < 5:
                    examples_ambiguous.append(
                        f"  {request_id} / {fact.source_id}: [{fact.fact_type.value}] {fact.ambiguity_note[:80]}"
                    )

            for fact in eb.facts:
                if fact.fact_type in {
                    FactType.SALARY_RAISE, FactType.SALARY_FIRST, FactType.INCOME_TERMINATED,
                    FactType.EXPENSE_NEW_RECURRING, FactType.EXPENSE_RENT_INCREASE,
                } and len(examples_message_only) < 5:
                    examples_message_only.append(
                        f"  {request_id}: [{fact.fact_type.value}] {fact.description[:90]}"
                    )

            for conflict in eb.conflicts:
                if len(examples_conflicts) < 5:
                    examples_conflicts.append(
                        f"  {request_id}: {conflict.note[:100]}"
                    )

        except Exception as e:
            errors.append(f"{request_id}: {type(e).__name__}: {e}")

    usage_summary = tracker.summary()

    print("=" * 70)
    print("PHASE 3 PRODUCTION EVIDENCE VALIDATION")
    print("=" * 70)
    print(f"\nTotal production requests examined: {total_requests}")
    print(f"Total messages processed:           {total_messages}")
    print(f"Total images processed:             {total_images}")
    print(f"\nExtraction method breakdown:")
    print(f"  Deterministic:                    {deterministic_count}")
    print(f"  AI text:                          {ai_text_count}")
    print(f"  AI VLM:                           {ai_vlm_count}")
    print(f"\nUnresolved evidence facts:          {unresolved_count}")
    print(f"Conflict records generated:         {conflict_count}")
    print(f"Requests with untrusted content:    {untrusted_count}")
    print(f"\nFact type distribution:")
    for ft, cnt in sorted(fact_type_counts.items(), key=lambda x: -x[1]):
        print(f"  {ft:45s} {cnt}")
    print(f"\nFact status distribution:")
    for st, cnt in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {st:20s} {cnt}")
    print(f"\nSource type distribution:")
    for src, cnt in sorted(source_type_counts.items(), key=lambda x: -x[1]):
        print(f"  {src:20s} {cnt}")
    print(f"\nModel usage:")
    for k, v in usage_summary.items():
        print(f"  {k}: {v}")
    if examples_message_only:
        print(f"\nExamples of message-only future facts:")
        for ex in examples_message_only:
            print(ex)
    if examples_unresolved:
        print(f"\nExamples of unresolved facts:")
        for ex in examples_unresolved:
            print(ex)
    if examples_ambiguous:
        print(f"\nExamples of ambiguous evidence:")
        for ex in examples_ambiguous:
            print(ex)
    if examples_conflicts:
        print(f"\nExamples of conflicts:")
        for ex in examples_conflicts:
            print(ex)
    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
    else:
        print(f"\nErrors: 0")
    print("\n" + "=" * 70)
    return errors


if __name__ == "__main__":
    errors = run_validation()
    sys.exit(0 if not errors else 1)
