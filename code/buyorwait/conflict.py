from __future__ import annotations

from .evidence import (
    ConflictRecord,
    ConflictReason,
    EvidenceFact,
    EvidenceSource,
    EvidenceStatus,
    FactType,
)


def resolve_conflicts(facts: list[EvidenceFact]) -> tuple[list[EvidenceFact], list[ConflictRecord]]:
    """
    Detect and resolve conflicts between evidence facts.
    Returns the deduplicated/resolved fact list and a record of every conflict.
    Conflict resolution order (from challenge spec §6.3):
    1. Explicit cancellation/settlement/amendment
    2. Newer record from the same source
    3. Settled event over estimate/forecast
    4. Financially safer interpretation
    """
    conflicts: list[ConflictRecord] = []
    active: list[EvidenceFact] = list(facts)

    cancellation_types = {FactType.EVENT_CANCELLATION, FactType.EVENT_AMENDMENT}
    settlement_types = {FactType.EVENT_SETTLEMENT_CONFIRMATION, FactType.REFUND_SETTLED}
    ignore_types = {FactType.IRRELEVANT, FactType.TRANSFER_INTERNAL}

    filtered: list[EvidenceFact] = []
    for fact in active:
        if fact.fact_type in ignore_types:
            continue
        filtered.append(fact)

    resolved: list[EvidenceFact] = []
    cancelled_event_ids: set[str] = set()

    for fact in filtered:
        if fact.fact_type == FactType.EVENT_CANCELLATION and fact.event_id:
            cancelled_event_ids.add(fact.event_id)

    for fact in filtered:
        if fact.event_id and fact.event_id in cancelled_event_ids and fact.fact_type not in cancellation_types:
            conflicts.append(ConflictRecord(
                fact_a=fact,
                fact_b=next(
                    (f for f in filtered if f.fact_type == FactType.EVENT_CANCELLATION and f.event_id == fact.event_id),
                    fact,
                ),
                resolved=True,
                selected_fact_id=None,
                reason=ConflictReason.EXPLICIT_CANCELLATION,
                note=f"Event {fact.event_id} has an explicit cancellation; original fact suppressed.",
            ))
            continue
        resolved.append(fact)

    event_id_groups: dict[str, list[EvidenceFact]] = {}
    for fact in resolved:
        if fact.event_id:
            event_id_groups.setdefault(fact.event_id, []).append(fact)

    final: list[EvidenceFact] = []
    used_event_ids_settled: set[str] = set()

    for event_id, group in event_id_groups.items():
        if len(group) == 1:
            final.append(group[0])
            continue

        settled = [f for f in group if f.status == EvidenceStatus.CONFIRMED]
        unresolved = [f for f in group if f.status == EvidenceStatus.UNRESOLVED]
        estimated = [f for f in group if f.status == EvidenceStatus.ESTIMATED]

        if settled and (unresolved or estimated):
            chosen = settled[0]
            for loser in group:
                if loser is not chosen:
                    conflicts.append(ConflictRecord(
                        fact_a=chosen,
                        fact_b=loser,
                        resolved=True,
                        selected_fact_id=chosen.fact_id,
                        reason=ConflictReason.SETTLED_OVER_ESTIMATE,
                        note=f"Settled fact preferred over estimated/unresolved for event {event_id}.",
                    ))
            final.append(chosen)
            used_event_ids_settled.add(event_id)
            continue

        if len(group) > 1:
            newest = max(group, key=lambda f: (f.source_id, f.fact_id))
            for loser in group:
                if loser is not newest:
                    conflicts.append(ConflictRecord(
                        fact_a=newest,
                        fact_b=loser,
                        resolved=True,
                        selected_fact_id=newest.fact_id,
                        reason=ConflictReason.NEWER_SAME_SOURCE,
                        note=f"Newest fact selected for event {event_id}.",
                    ))
            final.append(newest)
            continue

        final.extend(group)

    for fact in resolved:
        if fact.event_id is None:
            final.append(fact)

    return final, conflicts


def detect_amount_conflicts(
    structured_amount: "Decimal | None",
    evidence_amount: "Decimal | None",
    event_id: str,
    structured_fact: EvidenceFact | None,
    evidence_fact: EvidenceFact | None,
) -> ConflictRecord | None:
    if structured_amount is None or evidence_amount is None:
        return None
    if structured_fact is None or evidence_fact is None:
        return None
    if structured_amount != evidence_amount:
        from decimal import Decimal
        if evidence_fact.status == EvidenceStatus.CONFIRMED:
            selected = evidence_fact.fact_id
            reason = ConflictReason.SETTLED_OVER_ESTIMATE
            note = f"Image/message evidence overrides blank structured amount for event {event_id}."
        else:
            selected = None
            reason = ConflictReason.AMBIGUOUS_AMOUNT
            note = f"Structured amount {structured_amount} differs from evidence {evidence_amount}; unresolved."
        return ConflictRecord(
            fact_a=structured_fact,
            fact_b=evidence_fact,
            resolved=selected is not None,
            selected_fact_id=selected,
            reason=reason,
            note=note,
        )
    return None
