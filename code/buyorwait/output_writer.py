from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .output_record import OUTPUT_COLUMNS, OutputRecord


def write_output_csv(records: Iterable[OutputRecord], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
        for record in records:
            writer.writerow(record.as_row())
    return path
