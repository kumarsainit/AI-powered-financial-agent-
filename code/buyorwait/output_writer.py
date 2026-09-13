from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterable

from .output_record import OUTPUT_COLUMNS, OutputRecord


def write_output_csv(records: Iterable[OutputRecord], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        os.chmod(tmp_path, 0o644)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(OUTPUT_COLUMNS)
            for record in records:
                writer.writerow(record.as_row())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return path
