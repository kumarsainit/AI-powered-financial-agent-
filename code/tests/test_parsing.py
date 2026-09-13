from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal

from buyorwait import parsing
from buyorwait.domain import EventStatus
from buyorwait.errors import DataIntegrityError


class ParsingTests(unittest.TestCase):
    def test_parse_date_valid(self) -> None:
        self.assertEqual(parsing.parse_date("2024-03-03", "field", "row"), date(2024, 3, 3))

    def test_parse_date_invalid(self) -> None:
        with self.assertRaises(DataIntegrityError):
            parsing.parse_date("03/03/2024", "field", "row")

    def test_parse_date_blank_raises(self) -> None:
        with self.assertRaises(DataIntegrityError):
            parsing.parse_date("", "field", "row")

    def test_parse_optional_date_blank_is_none(self) -> None:
        self.assertIsNone(parsing.parse_optional_date("", "field", "row"))
        self.assertIsNone(parsing.parse_optional_date(None, "field", "row"))

    def test_parse_datetime(self) -> None:
        result = parsing.parse_datetime("2025-07-29T09:30:00Z", "field", "row")
        self.assertEqual(result, datetime.fromisoformat("2025-07-29T09:30:00+00:00"))

    def test_parse_decimal_required_blank_raises(self) -> None:
        with self.assertRaises(DataIntegrityError):
            parsing.parse_decimal("", "amount", "row")

    def test_parse_decimal_invalid_raises(self) -> None:
        with self.assertRaises(DataIntegrityError):
            parsing.parse_decimal("not-a-number", "amount", "row")

    def test_parse_decimal_valid(self) -> None:
        self.assertEqual(parsing.parse_decimal("1213.78", "amount", "row"), Decimal("1213.78"))

    def test_parse_optional_decimal_blank_is_none_not_zero(self) -> None:
        result = parsing.parse_optional_decimal("", "amount", "row")
        self.assertIsNone(result)
        self.assertNotEqual(result, Decimal("0"))

    def test_parse_bool(self) -> None:
        self.assertTrue(parsing.parse_bool("true", "field", "row"))
        self.assertFalse(parsing.parse_bool("false", "field", "row"))

    def test_parse_bool_invalid(self) -> None:
        with self.assertRaises(DataIntegrityError):
            parsing.parse_bool("yes", "field", "row")

    def test_parse_pipe_list(self) -> None:
        self.assertEqual(parsing.parse_pipe_list("rent|education|groceries"), ("rent", "education", "groceries"))
        self.assertEqual(parsing.parse_pipe_list(""), ())

    def test_parse_enum_valid(self) -> None:
        self.assertEqual(parsing.parse_enum(EventStatus, "settled", "status", "row"), EventStatus.SETTLED)

    def test_parse_enum_invalid_value_fails_clearly(self) -> None:
        with self.assertRaises(DataIntegrityError):
            parsing.parse_enum(EventStatus, "not-a-status", "status", "row")


if __name__ == "__main__":
    unittest.main()
