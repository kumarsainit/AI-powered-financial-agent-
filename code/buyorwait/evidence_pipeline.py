from __future__ import annotations

from .bundle import RequestBundle
from .conflict import resolve_conflicts
from .domain import ImageEvidence, Message
from .evidence import (
    EvidenceBundle,
    EvidenceFact,
    EvidenceSource,
    EvidenceStatus,
    ExtractionMethod,
    FactType,
)
from .image_extractor import extract_image_fact
from .message_extractor import extract_message_facts
from .usage import UsageTracker


def _get_event_description(bundle: RequestBundle, event_id: str | None) -> str:
    if event_id is None:
        return "financial event"
    event = next((e for e in bundle.events if e.event_id == event_id), None)
    if event is None:
        return f"event {event_id}"
    return event.description


def build_evidence_bundle(
    bundle: RequestBundle,
    tracker: UsageTracker,
) -> EvidenceBundle:
    request_id = bundle.request.request_id
    user_id = bundle.request.user_id
    known_event_ids = frozenset(e.event_id for e in bundle.events)

    all_facts: list[EvidenceFact] = []
    methods_used: set[ExtractionMethod] = set()

    for message in bundle.messages:
        facts = extract_message_facts(message, known_event_ids, request_id=request_id)
        all_facts.extend(facts)
        for f in facts:
            methods_used.add(f.extraction_method)

    for image in bundle.images:
        event_desc = _get_event_description(bundle, image.related_event_id)
        fact = extract_image_fact(image, event_desc, tracker, request_id=request_id)
        all_facts.append(fact)
        methods_used.add(fact.extraction_method)

    resolved_facts, conflicts = resolve_conflicts(all_facts)

    unresolved_count = sum(
        1 for f in resolved_facts if f.status == EvidenceStatus.UNRESOLVED
    )
    has_untrusted = any(not f.is_trusted for f in resolved_facts)

    return EvidenceBundle(
        request_id=request_id,
        user_id=user_id,
        facts=tuple(resolved_facts),
        conflicts=tuple(conflicts),
        unresolved_count=unresolved_count,
        has_untrusted_content=has_untrusted,
        extraction_methods_used=tuple(sorted(methods_used, key=lambda m: m.value)),
    )
