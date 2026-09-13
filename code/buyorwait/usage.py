from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass
class ModelUsageRecord:
    evidence_id: str
    provider: str
    model: str
    call_count: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimated_cost_usd: float | None
    cache_hit: bool
    extraction_success: bool
    error_message: str | None = None


class UsageTracker:
    def __init__(self) -> None:
        self._records: list[ModelUsageRecord] = []

    def record(self, rec: ModelUsageRecord) -> None:
        self._records.append(rec)

    def all_records(self) -> list[ModelUsageRecord]:
        return list(self._records)

    def total_calls(self) -> int:
        return sum(r.call_count for r in self._records)

    def cache_hits(self) -> int:
        return sum(1 for r in self._records if r.cache_hit)

    def cache_misses(self) -> int:
        return sum(1 for r in self._records if not r.cache_hit)

    def total_input_tokens(self) -> int:
        return sum(r.input_tokens or 0 for r in self._records)

    def total_output_tokens(self) -> int:
        return sum(r.output_tokens or 0 for r in self._records)

    def total_estimated_cost_usd(self) -> float:
        return sum(r.estimated_cost_usd or 0.0 for r in self._records)

    def summary(self) -> dict[str, Any]:
        total = self.total_calls()
        return {
            "total_model_calls": total,
            "cache_hits": self.cache_hits(),
            "cache_misses": self.cache_misses(),
            "total_input_tokens": self.total_input_tokens(),
            "total_output_tokens": self.total_output_tokens(),
            "total_estimated_cost_usd": round(self.total_estimated_cost_usd(), 6),
        }


_CACHE_DIR_ENV = "BUYORWAIT_CACHE_DIR"
_DEFAULT_CACHE_DIR = Path(".buyorwait_cache")


def _cache_dir() -> Path:
    d = Path(os.environ.get(_CACHE_DIR_ENV, _DEFAULT_CACHE_DIR))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]


def cache_get(key: str) -> dict | None:
    path = _cache_dir() / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def cache_put(key: str, value: dict) -> None:
    path = _cache_dir() / f"{key}.json"
    try:
        path.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass


def make_cache_key(prefix: str, content: str) -> str:
    return f"{prefix}_{_content_hash(content)}"
