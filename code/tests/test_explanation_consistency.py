from __future__ import annotations

import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.decision import AffordabilityStatus, ChangeAction, RecommendedMethod
from buyorwait.formatting import format_long_date, format_money_words
from buyorwait.pipeline import run_pipeline
from tests.paths import DATASET_DIR

_MONEY = re.compile(r"\b([A-Z]{3}) ((?:[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)(?:\.[0-9]{2})?)(?![0-9,.])")
_COUNT = re.compile(r"\b(\d+) installments\b")


@pytest.fixture(scope="module")
def pipeline():
    return run_pipeline(DATASET_DIR, None)


def supported_amounts(result) -> set[Decimal]:
    recommendation = result.recommendation
    spec = result.spec
    amounts = {
        spec.requested_amount_home,
        spec.minimum_balance_to_keep,
        recommendation.amount_safe_to_pay,
    }
    amounts.update(amount for _when, amount in recommendation.payment_plan)
    amounts.update(change.new_amount for change in recommendation.spending_changes)
    if spec.requested_amount_home is not None:
        amounts.add(spec.requested_amount_home - recommendation.amount_safe_to_pay)
    return {a for a in amounts if a is not None}


def test_every_money_figure_in_an_explanation_comes_from_the_decision(pipeline):
    offenders = []
    for result in pipeline.results:
        text = result.record.decision_explanation
        allowed = supported_amounts(result)
        allowed_text = {format_money_words(a, result.spec.home_currency) for a in allowed}
        for match in _MONEY.finditer(text):
            rendered = f"{match.group(1)} {match.group(2)}"
            if rendered not in allowed_text:
                offenders.append((result.request_id, rendered))
    assert not offenders, offenders[:10]


def test_every_currency_code_matches_the_user_home_currency(pipeline):
    for result in pipeline.results:
        for match in _MONEY.finditer(result.record.decision_explanation):
            assert match.group(1) == result.spec.home_currency


def test_every_date_in_an_explanation_comes_from_the_decision(pipeline):
    for result in pipeline.results:
        text = result.record.decision_explanation
        allowed = {format_long_date(when) for when, _amount in result.recommendation.payment_plan}
        allowed.add(format_long_date(result.spec.desired_completion_date))
        if result.recommendation.earliest_date_for_full_payment is not None:
            allowed.add(format_long_date(result.recommendation.earliest_date_for_full_payment))
        for candidate in re.finditer(r"\b(\d{1,2} [A-Z][a-z]+ \d{4})\b", text):
            assert candidate.group(1) in allowed, (result.request_id, candidate.group(1))


def test_installment_counts_in_explanations_match_the_plan(pipeline):
    for result in pipeline.results:
        match = _COUNT.search(result.record.decision_explanation)
        if match is None:
            continue
        assert result.recommendation.recommended_payment_method is RecommendedMethod.INSTALLMENTS
        assert int(match.group(1)) == len(result.recommendation.payment_plan)


def test_explanations_only_promise_payment_when_a_plan_exists(pipeline):
    for result in pipeline.results:
        text = result.record.decision_explanation.lower()
        if result.recommendation.recommended_payment_method is RecommendedMethod.NOT_RECOMMENDED:
            assert text.startswith("do not")
        else:
            assert not text.startswith("do not")


def test_spending_change_wording_matches_the_selected_changes(pipeline):
    for result in pipeline.results:
        text = result.record.decision_explanation.lower()
        stops = [c for c in result.recommendation.spending_changes if c.action is ChangeAction.STOP]
        reductions = [c for c in result.recommendation.spending_changes if c.action is ChangeAction.REDUCE_TO]
        assert text.count("stop the ") == len(stops)
        assert text.count("reduce the ") == len(reductions)


def test_uncertainty_wording_appears_exactly_when_obligations_are_unresolved(pipeline):
    for result in pipeline.results:
        text = result.record.decision_explanation
        has_clause = "no confirmed amount yet" in text
        assert has_clause == bool(result.recommendation.explanation_facts.blocking_obligations)


def test_affordable_now_explanations_never_mention_waiting(pipeline):
    for result in pipeline.results:
        if result.recommendation.affordability_status is not AffordabilityStatus.AFFORDABLE_NOW:
            continue
        text = result.record.decision_explanation.lower()
        assert "wait" not in text
        assert "in full on" not in text
