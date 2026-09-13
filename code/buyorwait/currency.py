from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable

from .domain import ExchangeRateRecord
from .errors import ExchangeRateNotFoundError

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class ExchangeRateTable:
    _rates: dict[tuple[date, str, str], Decimal] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: Iterable[ExchangeRateRecord]) -> "ExchangeRateTable":
        table: dict[tuple[date, str, str], Decimal] = {}
        for record in records:
            table[(record.rate_date, record.from_currency, record.to_currency)] = record.rate
        return cls(table)

    def rate(self, rate_date: date, from_currency: str, to_currency: str) -> Decimal:
        if from_currency == to_currency:
            return Decimal("1")
        key = (rate_date, from_currency, to_currency)
        if key not in self._rates:
            raise ExchangeRateNotFoundError(
                f"no exchange rate for {from_currency}->{to_currency} on {rate_date.isoformat()}"
            )
        return self._rates[key]

    def convert(self, amount: Decimal, rate_date: date, from_currency: str, to_currency: str) -> Decimal:
        if from_currency == to_currency:
            return amount
        rate = self.rate(rate_date, from_currency, to_currency)
        return (amount * rate).quantize(_CENT)
