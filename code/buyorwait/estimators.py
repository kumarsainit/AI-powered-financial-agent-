from __future__ import annotations

import statistics
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence

CENT = Decimal("0.01")


class AmountStatistic(Enum):
    MEAN = "mean"
    MEDIAN = "median"
    RECENT_MEAN = "recent_mean"
    RECENT_MEDIAN = "recent_median"
    TRIMMED_MEAN = "trimmed_mean"
    PERCENTILE_25 = "percentile_25"
    PERCENTILE_75 = "percentile_75"
    PERCENTILE_90 = "percentile_90"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    RECENT_MAXIMUM = "recent_maximum"
    RECENT_MINIMUM = "recent_minimum"
    LAST_OBSERVED = "last_observed"


def quantize(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def _percentile(values: Sequence[Decimal], fraction: Decimal) -> Decimal:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (Decimal(len(ordered)) - Decimal("1")) * fraction
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - Decimal(lower_index)
    lower = ordered[lower_index]
    upper = ordered[upper_index]
    return lower + (upper - lower) * weight


def _trimmed_mean(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    if len(ordered) <= 2:
        return sum(ordered, start=Decimal("0")) / Decimal(len(ordered))
    trim = len(ordered) // 10
    if trim:
        ordered = ordered[trim:-trim] or ordered
    return sum(ordered, start=Decimal("0")) / Decimal(len(ordered))


def estimate_amount(
    chronological_amounts: Sequence[Decimal],
    statistic: AmountStatistic,
    recent_window: int,
) -> Decimal:
    if not chronological_amounts:
        raise ValueError("cannot estimate an amount from an empty observation series")
    values = list(chronological_amounts)
    recent = values[-recent_window:] if recent_window > 0 else values

    if statistic is AmountStatistic.MEAN:
        result = sum(values, start=Decimal("0")) / Decimal(len(values))
    elif statistic is AmountStatistic.MEDIAN:
        result = Decimal(statistics.median(values))
    elif statistic is AmountStatistic.RECENT_MEAN:
        result = sum(recent, start=Decimal("0")) / Decimal(len(recent))
    elif statistic is AmountStatistic.RECENT_MEDIAN:
        result = Decimal(statistics.median(recent))
    elif statistic is AmountStatistic.TRIMMED_MEAN:
        result = _trimmed_mean(values)
    elif statistic is AmountStatistic.PERCENTILE_25:
        result = _percentile(values, Decimal("0.25"))
    elif statistic is AmountStatistic.PERCENTILE_75:
        result = _percentile(values, Decimal("0.75"))
    elif statistic is AmountStatistic.PERCENTILE_90:
        result = _percentile(values, Decimal("0.90"))
    elif statistic is AmountStatistic.MINIMUM:
        result = min(values)
    elif statistic is AmountStatistic.MAXIMUM:
        result = max(values)
    elif statistic is AmountStatistic.RECENT_MAXIMUM:
        result = max(recent)
    elif statistic is AmountStatistic.RECENT_MINIMUM:
        result = min(recent)
    elif statistic is AmountStatistic.LAST_OBSERVED:
        result = values[-1]
    else:
        raise ValueError(f"unsupported statistic: {statistic}")

    return quantize(Decimal(result))
