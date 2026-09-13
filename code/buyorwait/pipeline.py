from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bundle import build_request_bundle
from .decision import PurchaseSpec, Recommendation
from .decision_invariants import check_recommendation_invariants
from .evidence_pipeline import build_evidence_bundle
from .financial_state import FinancialStateForecast
from .forecast import build_financial_state
from .forecast_config import ForecastConfig
from .ingestion import Dataset, load_dataset, load_sample_request_user_ids
from .invariants import check_forecast_invariants
from .output_record import OutputRecord, assemble_output_record
from .output_writer import write_output_csv
from .planning import DecisionConfig, build_recommendation, normalize_request
from .usage import UsageTracker


@dataclass(frozen=True)
class RequestResult:
    request_id: str
    spec: PurchaseSpec
    forecast: FinancialStateForecast
    recommendation: Recommendation
    record: OutputRecord


@dataclass(frozen=True)
class PipelineResult:
    dataset: Dataset
    results: tuple[RequestResult, ...]
    output_path: Path | None
    tracker: UsageTracker

    @property
    def records(self) -> tuple[OutputRecord, ...]:
        return tuple(result.record for result in self.results)

    @property
    def recommendations(self) -> dict[str, Recommendation]:
        return {result.request_id: result.recommendation for result in self.results}


def sorted_request_ids(dataset: Dataset) -> tuple[str, ...]:
    def key(request_id: str) -> tuple:
        suffix = request_id.rsplit("_", 1)[-1]
        return (0, int(suffix)) if suffix.isdigit() else (1, request_id)

    return tuple(sorted(dataset.requests, key=key))


def process_request(
    dataset: Dataset,
    request_id: str,
    tracker: UsageTracker,
    forecast_config: ForecastConfig,
    decision_config: DecisionConfig,
) -> RequestResult:
    bundle = build_request_bundle(dataset, request_id, recurrence_config=forecast_config.recurrence)
    evidence = build_evidence_bundle(bundle, tracker)
    forecast = build_financial_state(bundle, evidence, forecast_config)
    check_forecast_invariants(forecast)
    spec = normalize_request(bundle, forecast)
    recommendation = build_recommendation(bundle, forecast, decision_config, spec)
    check_recommendation_invariants(recommendation, forecast, spec)
    record = assemble_output_record(recommendation, spec)
    return RequestResult(
        request_id=request_id,
        spec=spec,
        forecast=forecast,
        recommendation=recommendation,
        record=record,
    )


def run_pipeline(
    dataset_dir: str | Path,
    output_path: str | Path | None,
    forecast_config: ForecastConfig = ForecastConfig(),
    decision_config: DecisionConfig = DecisionConfig(),
) -> PipelineResult:
    dataset = load_dataset(dataset_dir)
    sample_users = load_sample_request_user_ids(dataset_dir)
    production_users = {request.user_id for request in dataset.requests.values()}
    leaked = production_users & sample_users
    if leaked:
        raise ValueError(f"sample users present in the production dataset: {sorted(leaked)[:5]}")

    tracker = UsageTracker()
    results = tuple(
        process_request(dataset, request_id, tracker, forecast_config, decision_config)
        for request_id in sorted_request_ids(dataset)
    )

    written: Path | None = None
    if output_path is not None:
        written = write_output_csv((result.record for result in results), output_path)

    return PipelineResult(dataset=dataset, results=results, output_path=written, tracker=tracker)
