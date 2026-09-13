from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .domain import (
    EventStatus,
    EventType,
    FinancialEvent,
    LifecycleChain,
    LifecycleMember,
    LifecyclePattern,
    MemberDisposition,
)

_PATTERN_TABLE: dict[tuple[EventType, EventStatus, EventType, EventStatus], LifecyclePattern] = {
    (EventType.REFUND, EventStatus.SETTLED, EventType.EXPENSE, EventStatus.SETTLED): LifecyclePattern.REFUND_SETTLED,
    (EventType.REFUND, EventStatus.PENDING, EventType.EXPENSE, EventStatus.SETTLED): LifecyclePattern.REFUND_PENDING,
    (EventType.EXPENSE, EventStatus.SETTLED, EventType.EXPENSE, EventStatus.CANCELLED): LifecyclePattern.CANCELLED_AUTHORIZATION,
    (EventType.DEBT_PAYMENT, EventStatus.SCHEDULED, EventType.DEBT_PAYMENT, EventStatus.FAILED): LifecyclePattern.FAILED_RETRY,
    (EventType.EXPENSE, EventStatus.PENDING, EventType.EXPENSE, EventStatus.SETTLED): LifecyclePattern.POSSIBLE_DUPLICATE,
    (EventType.INVESTMENT_VALUATION, EventStatus.UNREALIZED, EventType.INVESTMENT_PURCHASE, EventStatus.SETTLED): LifecyclePattern.UNREALIZED_VALUATION,
    (EventType.INVESTMENT_SALE, EventStatus.SETTLED, EventType.INVESTMENT_PURCHASE, EventStatus.SETTLED): LifecyclePattern.REALIZED_SALE,
}

_PATTERN_DISPOSITIONS: dict[LifecyclePattern, tuple[MemberDisposition, MemberDisposition]] = {
    LifecyclePattern.REFUND_SETTLED: (MemberDisposition.INCLUDE, MemberDisposition.INCLUDE),
    LifecyclePattern.REFUND_PENDING: (MemberDisposition.EXCLUDE_PENDING_CREDIT, MemberDisposition.INCLUDE),
    LifecyclePattern.CANCELLED_AUTHORIZATION: (MemberDisposition.INCLUDE, MemberDisposition.EXCLUDE_SUPERSEDED),
    LifecyclePattern.FAILED_RETRY: (MemberDisposition.INCLUDE, MemberDisposition.EXCLUDE_SUPERSEDED),
    LifecyclePattern.POSSIBLE_DUPLICATE: (MemberDisposition.EXCLUDE_SUPERSEDED, MemberDisposition.INCLUDE),
    LifecyclePattern.UNREALIZED_VALUATION: (MemberDisposition.EXCLUDE_NONCASH, MemberDisposition.INCLUDE),
    LifecyclePattern.REALIZED_SALE: (MemberDisposition.INCLUDE, MemberDisposition.INCLUDE),
    LifecyclePattern.UNCLASSIFIED_LINK: (MemberDisposition.INCLUDE, MemberDisposition.INCLUDE),
}


def _connected_component(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    stack = [start]
    seen = {start}
    while stack:
        node = stack.pop()
        for neighbor in adjacency.get(node, ()):
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen


def _build_chain(component: set[str], by_id: dict[str, FinancialEvent]) -> LifecycleChain:
    if len(component) == 1:
        (only,) = tuple(component)
        return LifecycleChain(
            chain_id=only,
            pattern=LifecyclePattern.STANDALONE,
            members=(LifecycleMember(only, "standalone", MemberDisposition.INCLUDE),),
        )

    if len(component) == 2:
        first_id, second_id = sorted(component)
        first, second = by_id[first_id], by_id[second_id]
        if first.linked_event_id == second_id:
            source, target = first, second
        else:
            source, target = second, first
        key = (source.event_type, source.status, target.event_type, target.status)
        pattern = _PATTERN_TABLE.get(key, LifecyclePattern.UNCLASSIFIED_LINK)
        source_disposition, target_disposition = _PATTERN_DISPOSITIONS[pattern]
        return LifecycleChain(
            chain_id=f"{source.event_id}->{target.event_id}",
            pattern=pattern,
            members=(
                LifecycleMember(source.event_id, "source", source_disposition),
                LifecycleMember(target.event_id, "target", target_disposition),
            ),
        )

    dispositions = {event_id: MemberDisposition.INCLUDE for event_id in component}
    for event_id in sorted(component):
        event = by_id[event_id]
        target_id = event.linked_event_id
        if target_id is None or target_id not in component:
            continue
        target = by_id[target_id]
        key = (event.event_type, event.status, target.event_type, target.status)
        pattern = _PATTERN_TABLE.get(key, LifecyclePattern.UNCLASSIFIED_LINK)
        source_disposition, target_disposition = _PATTERN_DISPOSITIONS[pattern]
        if source_disposition is not MemberDisposition.INCLUDE:
            dispositions[event_id] = source_disposition
        if target_disposition is not MemberDisposition.INCLUDE:
            dispositions[target_id] = target_disposition

    members = tuple(
        LifecycleMember(event_id, "component", dispositions[event_id]) for event_id in sorted(component)
    )
    return LifecycleChain(
        chain_id="|".join(sorted(component)),
        pattern=LifecyclePattern.UNCLASSIFIED_LINK,
        members=members,
    )


def resolve_lifecycle(events: Iterable[FinancialEvent]) -> tuple[LifecycleChain, ...]:
    events = tuple(events)
    by_id = {event.event_id: event for event in events}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for event in events:
        if event.linked_event_id is not None and event.linked_event_id in by_id:
            adjacency[event.event_id].add(event.linked_event_id)
            adjacency[event.linked_event_id].add(event.event_id)

    visited: set[str] = set()
    chains = []
    for event in events:
        if event.event_id in visited:
            continue
        component = _connected_component(event.event_id, adjacency)
        visited |= component
        chains.append(_build_chain(component, by_id))
    return tuple(chains)
