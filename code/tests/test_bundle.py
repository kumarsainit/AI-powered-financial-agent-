from __future__ import annotations

import unittest

from buyorwait import ingestion
from buyorwait.bundle import build_request_bundle
from buyorwait.domain import Flexibility, MemberDisposition
from buyorwait.errors import UnknownRequestError

from .paths import DATASET_DIR


class RequestIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = ingestion.load_dataset(DATASET_DIR)
        cls.sample_request_ids = ingestion.load_sample_request_ids(DATASET_DIR)
        cls.sample_user_ids = ingestion.load_sample_request_user_ids(DATASET_DIR)

    def test_only_production_requests_are_loaded(self) -> None:
        self.assertTrue(self.sample_request_ids.isdisjoint(self.dataset.requests.keys()))

    def test_sample_users_never_appear_as_a_bundle_subject(self) -> None:
        for request in self.dataset.requests.values():
            self.assertNotIn(request.user_id, self.sample_user_ids)

    def test_unknown_request_id_raises(self) -> None:
        with self.assertRaises(UnknownRequestError):
            build_request_bundle(self.dataset, "request_does_not_exist")

    def test_sample_only_request_id_is_not_buildable(self) -> None:
        sample_only_id = next(iter(self.sample_request_ids - set(self.dataset.requests.keys())))
        with self.assertRaises(UnknownRequestError):
            build_request_bundle(self.dataset, sample_only_id)


class RequestBundleAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = ingestion.load_dataset(DATASET_DIR)

    def test_bundle_builds_for_every_production_request(self) -> None:
        for request_id in self.dataset.requests:
            bundle = build_request_bundle(self.dataset, request_id)
            self.assertEqual(bundle.request.request_id, request_id)
            self.assertEqual(bundle.profile.user_id, bundle.request.user_id)

    def test_bundle_events_all_belong_to_the_requesting_user(self) -> None:
        for request_id in list(self.dataset.requests)[:20]:
            bundle = build_request_bundle(self.dataset, request_id)
            for event in bundle.events:
                self.assertEqual(event.user_id, bundle.request.user_id)

    def test_lifecycle_never_double_counts_a_chain(self) -> None:
        for request_id in list(self.dataset.requests)[:20]:
            bundle = build_request_bundle(self.dataset, request_id)
            seen = set()
            for chain in bundle.lifecycle_chains:
                for event_id in chain.included_event_ids():
                    self.assertNotIn(event_id, seen)
                    seen.add(event_id)

    def test_spending_change_candidates_respect_minimum_allowed_amount_floor(self) -> None:
        for request_id in self.dataset.requests:
            bundle = build_request_bundle(self.dataset, request_id)
            for candidate in bundle.spending_change_candidates:
                if candidate.flexibility in (Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE):
                    self.assertIsNotNone(candidate.minimum_allowed_amount)
                    if candidate.current_amount is not None:
                        self.assertLessEqual(candidate.minimum_allowed_amount, candidate.current_amount)

    def test_exchange_rates_are_shared_not_copied(self) -> None:
        bundle_a = build_request_bundle(self.dataset, next(iter(self.dataset.requests)))
        bundle_b = build_request_bundle(self.dataset, list(self.dataset.requests)[1])
        self.assertIs(bundle_a.exchange_rates, bundle_b.exchange_rates)
        self.assertIs(bundle_a.exchange_rates, self.dataset.exchange_rates)


if __name__ == "__main__":
    unittest.main()
