from __future__ import annotations

import csv
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from buyorwait import ingestion
from buyorwait.domain import Direction, EventStatus, EventType, Flexibility
from buyorwait.errors import DataIntegrityError

from .paths import DATASET_DIR


class RealDatasetLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = ingestion.load_dataset(DATASET_DIR)

    def test_valid_rows_are_loaded(self) -> None:
        self.assertEqual(len(self.dataset.requests), 250)
        self.assertGreater(len(self.dataset.events), 0)
        self.assertGreater(len(self.dataset.profiles), 0)

    def test_production_requests_scope_matches_requests_csv(self) -> None:
        with (DATASET_DIR / "requests.csv").open(newline="", encoding="utf-8") as handle:
            expected_ids = {row["request_id"] for row in csv.DictReader(handle)}
        self.assertEqual(set(self.dataset.requests.keys()), expected_ids)

    def test_missing_amount_is_none_not_zero(self) -> None:
        blank_amount_events = [e for e in self.dataset.events.values() if e.amount is None]
        self.assertGreater(len(blank_amount_events), 0)
        for event in blank_amount_events:
            self.assertIsNone(event.amount)

    def test_date_fields_parsed_as_dates(self) -> None:
        sample_request = next(iter(self.dataset.requests.values()))
        self.assertIsInstance(sample_request.request_date.year, int)

    def test_currency_fields_are_populated(self) -> None:
        known_currencies = {"INR", "EUR", "IDR", "ZAR", "USD"}
        currencies = {p.home_currency for p in self.dataset.profiles.values()}
        self.assertTrue(currencies.issubset(known_currencies))
        self.assertTrue(len(currencies) > 0)

    def test_event_enum_fields_typed(self) -> None:
        sample_event = next(iter(self.dataset.events.values()))
        self.assertIsInstance(sample_event.event_type, EventType)
        self.assertIsInstance(sample_event.direction, Direction)
        self.assertIsInstance(sample_event.status, EventStatus)
        self.assertIsInstance(sample_event.flexibility, Flexibility)

    def test_joins_request_to_profile(self) -> None:
        for request in self.dataset.requests.values():
            self.assertIn(request.user_id, self.dataset.profiles)

    def test_joins_payment_options_to_requests(self) -> None:
        for request_id, options in self.dataset.payment_options_by_request.items():
            self.assertIn(request_id, self.dataset.requests)
            self.assertTrue(len(options) >= 2)

    def test_joins_events_to_users(self) -> None:
        for event in self.dataset.events.values():
            self.assertIn(event.user_id, self.dataset.profiles)

    def test_minimum_allowed_amount_only_on_reducible_events(self) -> None:
        for event in self.dataset.events.values():
            if event.minimum_allowed_amount is not None:
                self.assertIn(event.flexibility, (Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE))

    def test_sample_users_excluded_from_production_dataset(self) -> None:
        sample_user_ids = ingestion.load_sample_request_user_ids(DATASET_DIR)
        self.assertTrue(sample_user_ids.isdisjoint(self.dataset.profiles.keys()))
        self.assertTrue(sample_user_ids.isdisjoint(self.dataset.events_by_user.keys()))


class MalformedRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        for name in (
            "requests.csv",
            "financial_profiles.csv",
            "financial_events.csv",
            "exchange_rates.csv",
            "request_payment_options.csv",
            "messages.csv",
            "images.csv",
        ):
            shutil.copy(DATASET_DIR / name, self.tmp_dir / name)
        (self.tmp_dir / "media").mkdir()
        (self.tmp_dir / "media" / "images").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _rewrite_events(self, mutate) -> None:
        path = self.tmp_dir / "financial_events.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            rows = list(reader)
        mutate(rows)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_invalid_monetary_value_fails_clearly(self) -> None:
        self._rewrite_events(lambda rows: rows.__setitem__(0, {**rows[0], "amount": "not-a-number"}))
        with self.assertRaises(DataIntegrityError):
            ingestion.load_dataset(self.tmp_dir)

    def test_missing_required_id_fails_clearly(self) -> None:
        self._rewrite_events(lambda rows: rows.__setitem__(0, {**rows[0], "event_id": ""}))
        with self.assertRaises(DataIntegrityError):
            ingestion.load_dataset(self.tmp_dir)

    def test_invalid_enum_value_fails_clearly(self) -> None:
        self._rewrite_events(lambda rows: rows.__setitem__(0, {**rows[0], "status": "not-a-status"}))
        with self.assertRaises(DataIntegrityError):
            ingestion.load_dataset(self.tmp_dir)

    def test_unresolvable_linked_event_id_fails_clearly(self) -> None:
        production_row_index = 2288
        self._rewrite_events(
            lambda rows: rows.__setitem__(
                production_row_index, {**rows[production_row_index], "linked_event_id": "event_does_not_exist"}
            )
        )
        with self.assertRaises(DataIntegrityError):
            ingestion.load_dataset(self.tmp_dir)

    def test_blank_amount_does_not_raise(self) -> None:
        self._rewrite_events(lambda rows: rows.__setitem__(0, {**rows[0], "amount": ""}))
        dataset = ingestion.load_dataset(self.tmp_dir)
        self.assertGreater(len(dataset.events), 0)


if __name__ == "__main__":
    unittest.main()
