from __future__ import annotations

from typing import Iterable

from .domain import EventStatus, Flexibility, FinancialEvent, RecurringEventCandidate, SpendingChangeCandidate, UserFinancialProfile
from .errors import DataIntegrityError

_ELIGIBLE_STATUSES = (EventStatus.SETTLED, EventStatus.SCHEDULED)


def build_spending_change_candidates(
    events: Iterable[FinancialEvent],
    profile: UserFinancialProfile,
    recurrence_candidates: Iterable[RecurringEventCandidate],
) -> tuple[SpendingChangeCandidate, ...]:
    recurrence_by_category = {
        candidate.category: candidate for candidate in recurrence_candidates if candidate.user_id == profile.user_id
    }

    candidates = []
    for event in events:
        if event.user_id != profile.user_id:
            raise DataIntegrityError("event belongs to a different user than the profile", event.event_id, "user_id", event.user_id)
        if event.flexibility is Flexibility.FIXED:
            continue
        if event.status not in _ELIGIBLE_STATUSES:
            continue

        candidates.append(
            SpendingChangeCandidate(
                event_id=event.event_id,
                user_id=event.user_id,
                category=event.category,
                flexibility=event.flexibility,
                current_amount=event.amount,
                minimum_allowed_amount=event.minimum_allowed_amount,
                user_permits_reduce=event.category in profile.expense_categories_user_is_willing_to_reduce,
                user_permits_stop=event.category in profile.expense_categories_user_is_willing_to_stop,
                is_protected=event.category in profile.expense_categories_to_protect,
                recurrence_classification=recurrence_by_category.get(event.category),
            )
        )

    return tuple(candidates)
