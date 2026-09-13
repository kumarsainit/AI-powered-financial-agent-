from __future__ import annotations

from dataclasses import dataclass

from .estimators import AmountStatistic
from .recurrence import RecurrenceDetectorConfig


@dataclass(frozen=True)
class ForecastConfig:
    horizon_days: int = 90
    recurrence: RecurrenceDetectorConfig = RecurrenceDetectorConfig()
    recent_window: int = 3
    fixed_expense_statistic: AmountStatistic = AmountStatistic.MEDIAN
    variable_essential_expense_statistic: AmountStatistic = AmountStatistic.MEDIAN
    flexible_expense_statistic: AmountStatistic = AmountStatistic.TRIMMED_MEAN
    income_statistic: AmountStatistic = AmountStatistic.PERCENTILE_25
    project_weak_expenses: bool = True
    project_weak_income: bool = True
    fixed_amount_tolerance: float = 0.01
    minimum_history_days: int = 0
    max_staleness_multiple: float = 1.0
    staleness_scope: str = "expense"
