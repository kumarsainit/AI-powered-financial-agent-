from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from buyorwait.currency import ExchangeRateTable
from buyorwait.domain import ExchangeRateRecord
from buyorwait.errors import ExchangeRateNotFoundError


def make_table() -> ExchangeRateTable:
    records = [
        ExchangeRateRecord(date(2024, 1, 15), "USD", "INR", Decimal("83.33")),
        ExchangeRateRecord(date(2024, 2, 15), "USD", "INR", Decimal("83.33")),
    ]
    return ExchangeRateTable.from_records(records)


class CurrencyTests(unittest.TestCase):
    def test_same_currency_returns_amount_unchanged(self) -> None:
        table = make_table()
        result = table.convert(Decimal("100.00"), date(2024, 1, 15), "USD", "USD")
        self.assertEqual(result, Decimal("100.00"))

    def test_same_currency_does_not_require_a_rate(self) -> None:
        table = ExchangeRateTable.from_records([])
        result = table.convert(Decimal("50"), date(2024, 1, 1), "EUR", "EUR")
        self.assertEqual(result, Decimal("50"))

    def test_conversion_uses_exact_rate(self) -> None:
        table = make_table()
        result = table.convert(Decimal("10"), date(2024, 1, 15), "USD", "INR")
        self.assertEqual(result, Decimal("833.30"))

    def test_missing_rate_raises_not_a_silent_default(self) -> None:
        table = make_table()
        with self.assertRaises(ExchangeRateNotFoundError):
            table.convert(Decimal("10"), date(2024, 1, 15), "USD", "ZAR")

    def test_date_specific_rate_lookup(self) -> None:
        table = ExchangeRateTable.from_records(
            [
                ExchangeRateRecord(date(2024, 1, 15), "USD", "INR", Decimal("83.00")),
                ExchangeRateRecord(date(2024, 2, 15), "USD", "INR", Decimal("84.00")),
            ]
        )
        self.assertEqual(table.rate(date(2024, 1, 15), "USD", "INR"), Decimal("83.00"))
        self.assertEqual(table.rate(date(2024, 2, 15), "USD", "INR"), Decimal("84.00"))

    def test_missing_date_raises_even_when_pair_exists_on_another_date(self) -> None:
        table = make_table()
        with self.assertRaises(ExchangeRateNotFoundError):
            table.rate(date(2024, 3, 15), "USD", "INR")


if __name__ == "__main__":
    unittest.main()
