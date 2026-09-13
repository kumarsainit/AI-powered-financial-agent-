from __future__ import annotations

from dataclasses import dataclass

from .currency import ExchangeRateTable
from .domain import (
    FinancialEvent,
    HistoricalSpendingObservation,
    ImageEvidence,
    LifecycleChain,
    Message,
    PaymentOption,
    RecurringEventCandidate,
    Request,
    SpendingChangeCandidate,
    UserFinancialProfile,
)
from .errors import UnknownRequestError
from .ingestion import Dataset
from .lifecycle import resolve_lifecycle
from .recurrence import RecurrenceDetectorConfig, detect_recurrence
from .spending_change import build_spending_change_candidates
from .spending_history import SpendingHistoryConfig, compute_spending_observations


@dataclass(frozen=True)
class RequestBundle:
    request: Request
    profile: UserFinancialProfile
    events: tuple[FinancialEvent, ...]
    lifecycle_chains: tuple[LifecycleChain, ...]
    included_event_ids: frozenset[str]
    recurrence_candidates: tuple[RecurringEventCandidate, ...]
    spending_observations: tuple[HistoricalSpendingObservation, ...]
    spending_change_candidates: tuple[SpendingChangeCandidate, ...]
    payment_options: tuple[PaymentOption, ...]
    messages: tuple[Message, ...]
    images: tuple[ImageEvidence, ...]
    exchange_rates: ExchangeRateTable


def build_request_bundle(
    dataset: Dataset,
    request_id: str,
    recurrence_config: RecurrenceDetectorConfig = RecurrenceDetectorConfig(),
    spending_history_config: SpendingHistoryConfig = SpendingHistoryConfig(),
) -> RequestBundle:
    request = dataset.requests.get(request_id)
    if request is None:
        raise UnknownRequestError(request_id)

    profile = dataset.profiles[request.user_id]
    events = dataset.events_by_user.get(request.user_id, ())

    lifecycle_chains = resolve_lifecycle(events)
    included_event_ids = frozenset(
        event_id for chain in lifecycle_chains for event_id in chain.included_event_ids()
    )
    included_events = tuple(event for event in events if event.event_id in included_event_ids)

    recurrence_candidates = detect_recurrence(included_events, recurrence_config)
    spending_observations = compute_spending_observations(included_events, spending_history_config)
    spending_change_candidates = build_spending_change_candidates(included_events, profile, recurrence_candidates)

    return RequestBundle(
        request=request,
        profile=profile,
        events=events,
        lifecycle_chains=lifecycle_chains,
        included_event_ids=included_event_ids,
        recurrence_candidates=recurrence_candidates,
        spending_observations=spending_observations,
        spending_change_candidates=spending_change_candidates,
        payment_options=dataset.payment_options_by_request.get(request_id, ()),
        messages=dataset.messages_by_request.get(request_id, ()),
        images=dataset.images_by_request.get(request_id, ()),
        exchange_rates=dataset.exchange_rates,
    )
