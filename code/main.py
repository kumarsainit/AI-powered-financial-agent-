#!/usr/bin/env python3
"""Buy or Wait? — deterministic end-to-end production entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from buyorwait.ingestion import load_sample_request_user_ids
from buyorwait.output_validator import require_valid_output, validate_output_file
from buyorwait.pipeline import run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "dataset"
DEFAULT_OUTPUT = REPO_ROOT / "output.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate output.csv for dataset/requests.csv")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--skip-validation", action="store_true")
    args = parser.parse_args(argv)

    result = run_pipeline(args.dataset, args.output)
    print(f"requests processed: {len(result.results)}")
    print(f"output written to: {result.output_path}")

    if not args.skip_validation:
        report = validate_output_file(
            args.output,
            result.dataset.requests,
            result.recommendations,
            load_sample_request_user_ids(args.dataset),
        )
        require_valid_output(report)
        print(f"output validation: {report.rows_checked} rows checked, 0 errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
