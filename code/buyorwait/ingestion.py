from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .currency import ExchangeRateTable
from .domain import (
    ExchangeRateRecord,
    FinancialEvent,
    ImageEvidence,
    Message,
    PaymentMethod,
    PaymentOption,
    Request,
    RequestType,
    UserFinancialProfile,
)
from .domain import Direction, EventStatus, EventType, Flexibility
from .errors import DataIntegrityError
from .parsing import (
    optional_str,
    parse_bool,
    parse_date,
    parse_optional_date,
    parse_datetime,
    parse_decimal,
    parse_enum,
    parse_int,
    parse_optional_decimal,
    parse_optional_int,
    parse_pipe_enum_list,
    parse_pipe_list,
    require_id,
)

REQUESTS_FILE = "requests.csv"
SAMPLE_REQUESTS_FILE = "sample_requests.csv"
PROFILES_FILE = "financial_profiles.csv"
EVENTS_FILE = "financial_events.csv"
EXCHANGE_RATES_FILE = "exchange_rates.csv"
PAYMENT_OPTIONS_FILE = "request_payment_options.csv"
MESSAGES_FILE = "messages.csv"
IMAGES_FILE = "images.csv"
IMAGES_DIR = Path("media") / "images"


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _profile_from_row(row: dict[str, str]) -> UserFinancialProfile:
    row_id = f"{PROFILES_FILE}:{row.get('user_id')}"
    return UserFinancialProfile(
        user_id=require_id(row.get("user_id"), "user_id", row_id),
        home_currency=require_id(row.get("home_currency"), "home_currency", row_id),
        current_available_balance=parse_decimal(row.get("current_available_balance"), "current_available_balance", row_id),
        minimum_balance_to_keep=parse_decimal(row.get("minimum_balance_to_keep"), "minimum_balance_to_keep", row_id),
        financial_priorities=parse_pipe_list(row.get("financial_priorities")),
        expense_categories_to_protect=parse_pipe_list(row.get("expense_categories_to_protect")),
        expense_categories_user_is_willing_to_reduce=parse_pipe_list(row.get("expense_categories_user_is_willing_to_reduce")),
        expense_categories_user_is_willing_to_stop=parse_pipe_list(row.get("expense_categories_user_is_willing_to_stop")),
        payment_methods_user_will_consider=parse_pipe_enum_list(
            PaymentMethod, row.get("payment_methods_user_will_consider"), "payment_methods_user_will_consider", row_id
        ),
        max_installment_months=parse_optional_int(row.get("max_installment_months"), "max_installment_months", row_id),
    )


def _request_from_row(row: dict[str, str]) -> Request:
    row_id = f"{REQUESTS_FILE}:{row.get('request_id')}"
    return Request(
        request_id=require_id(row.get("request_id"), "request_id", row_id),
        user_id=require_id(row.get("user_id"), "user_id", row_id),
        request_date=parse_date(row.get("request_date"), "request_date", row_id),
        request_type=parse_enum(RequestType, row.get("request_type"), "request_type", row_id),
        requested_amount=parse_decimal(row.get("requested_amount"), "requested_amount", row_id),
        desired_completion_date=parse_date(row.get("desired_completion_date"), "desired_completion_date", row_id),
        allows_partial_payment=parse_bool(row.get("allows_partial_payment"), "allows_partial_payment", row_id),
        request_text=require_id(row.get("request_text"), "request_text", row_id),
    )


def _event_from_row(row: dict[str, str]) -> FinancialEvent:
    row_id = f"{EVENTS_FILE}:{row.get('event_id')}"
    return FinancialEvent(
        event_id=require_id(row.get("event_id"), "event_id", row_id),
        user_id=require_id(row.get("user_id"), "user_id", row_id),
        event_type=parse_enum(EventType, row.get("event_type"), "event_type", row_id),
        description=require_id(row.get("description"), "description", row_id),
        category=require_id(row.get("category"), "category", row_id),
        direction=parse_enum(Direction, row.get("direction"), "direction", row_id),
        amount=parse_optional_decimal(row.get("amount"), "amount", row_id),
        currency=require_id(row.get("currency"), "currency", row_id),
        event_date=parse_date(row.get("event_date"), "event_date", row_id),
        settlement_date=parse_optional_date(row.get("settlement_date"), "settlement_date", row_id),
        status=parse_enum(EventStatus, row.get("status"), "status", row_id),
        linked_event_id=optional_str(row.get("linked_event_id")),
        flexibility=parse_enum(Flexibility, row.get("flexibility"), "flexibility", row_id),
        minimum_allowed_amount=parse_optional_decimal(row.get("minimum_allowed_amount"), "minimum_allowed_amount", row_id),
    )


def _exchange_rate_from_row(row: dict[str, str]) -> ExchangeRateRecord:
    row_id = f"{EXCHANGE_RATES_FILE}:{row.get('rate_date')}:{row.get('from_currency')}:{row.get('to_currency')}"
    return ExchangeRateRecord(
        rate_date=parse_date(row.get("rate_date"), "rate_date", row_id),
        from_currency=require_id(row.get("from_currency"), "from_currency", row_id),
        to_currency=require_id(row.get("to_currency"), "to_currency", row_id),
        rate=parse_decimal(row.get("rate"), "rate", row_id),
    )


def _payment_option_from_row(row: dict[str, str]) -> PaymentOption:
    row_id = f"{PAYMENT_OPTIONS_FILE}:{row.get('payment_option_id')}"
    return PaymentOption(
        payment_option_id=require_id(row.get("payment_option_id"), "payment_option_id", row_id),
        request_id=require_id(row.get("request_id"), "request_id", row_id),
        payment_method=parse_enum(PaymentMethod, row.get("payment_method"), "payment_method", row_id),
        payment_amount=parse_decimal(row.get("payment_amount"), "payment_amount", row_id),
        number_of_payments=parse_int(row.get("number_of_payments"), "number_of_payments", row_id),
        first_payment_date=parse_date(row.get("first_payment_date"), "first_payment_date", row_id),
        payment_frequency_days=parse_optional_int(row.get("payment_frequency_days"), "payment_frequency_days", row_id),
        financing_fee=parse_decimal(row.get("financing_fee"), "financing_fee", row_id),
        total_payable_amount=parse_decimal(row.get("total_payable_amount"), "total_payable_amount", row_id),
    )


def _message_from_row(row: dict[str, str]) -> Message:
    row_id = f"{MESSAGES_FILE}:{row.get('message_id')}"
    return Message(
        message_id=require_id(row.get("message_id"), "message_id", row_id),
        user_id=require_id(row.get("user_id"), "user_id", row_id),
        request_id=optional_str(row.get("request_id")),
        related_event_id=optional_str(row.get("related_event_id")),
        sent_at=parse_datetime(row.get("sent_at"), "sent_at", row_id),
        source_type=require_id(row.get("source_type"), "source_type", row_id),
        message_text=require_id(row.get("message_text"), "message_text", row_id),
    )


def _image_from_row(row: dict[str, str], dataset_dir: Path) -> ImageEvidence:
    row_id = f"{IMAGES_FILE}:{row.get('image_id')}"
    image_id = require_id(row.get("image_id"), "image_id", row_id)
    return ImageEvidence(
        image_id=image_id,
        user_id=require_id(row.get("user_id"), "user_id", row_id),
        request_id=optional_str(row.get("request_id")),
        related_event_id=optional_str(row.get("related_event_id")),
        file_path=str(dataset_dir / IMAGES_DIR / f"{image_id}.png"),
    )


@dataclass(frozen=True)
class Dataset:
    profiles: dict[str, UserFinancialProfile]
    requests: dict[str, Request]
    events: dict[str, FinancialEvent]
    events_by_user: dict[str, tuple[FinancialEvent, ...]]
    payment_options_by_request: dict[str, tuple[PaymentOption, ...]]
    messages_by_user: dict[str, tuple[Message, ...]]
    messages_by_request: dict[str, tuple[Message, ...]]
    images_by_request: dict[str, tuple[ImageEvidence, ...]]
    exchange_rates: ExchangeRateTable


def _validate_relationships(dataset: Dataset) -> None:
    for request in dataset.requests.values():
        if request.user_id not in dataset.profiles:
            raise DataIntegrityError("references unknown user", request.request_id, "user_id", request.user_id)

    for event in dataset.events.values():
        if event.user_id not in dataset.profiles:
            raise DataIntegrityError("references unknown user", event.event_id, "user_id", event.user_id)
        if event.linked_event_id is not None:
            linked = dataset.events.get(event.linked_event_id)
            if linked is None:
                raise DataIntegrityError("references unknown linked event", event.event_id, "linked_event_id", event.linked_event_id)
            if linked.user_id != event.user_id:
                raise DataIntegrityError("linked event belongs to a different user", event.event_id, "linked_event_id", event.linked_event_id)

    for options in dataset.payment_options_by_request.values():
        for option in options:
            if option.request_id not in dataset.requests:
                raise DataIntegrityError("references unknown request", option.payment_option_id, "request_id", option.request_id)

    for messages in dataset.messages_by_user.values():
        for message in messages:
            if message.user_id not in dataset.profiles:
                raise DataIntegrityError("references unknown user", message.message_id, "user_id", message.user_id)
            if message.request_id is not None and message.request_id not in dataset.requests:
                raise DataIntegrityError("references unknown request", message.message_id, "request_id", message.request_id)
            if message.related_event_id is not None and message.related_event_id not in dataset.events:
                raise DataIntegrityError("references unknown event", message.message_id, "related_event_id", message.related_event_id)

    for images in dataset.images_by_request.values():
        for image in images:
            if image.user_id not in dataset.profiles:
                raise DataIntegrityError("references unknown user", image.image_id, "user_id", image.user_id)
            if image.related_event_id is not None and image.related_event_id not in dataset.events:
                raise DataIntegrityError("references unknown event", image.image_id, "related_event_id", image.related_event_id)


def load_dataset(dataset_dir: str | Path) -> Dataset:
    dataset_dir = Path(dataset_dir)

    requests = {row.request_id: row for row in (_request_from_row(r) for r in _read_rows(dataset_dir / REQUESTS_FILE))}
    production_user_ids = {request.user_id for request in requests.values()}

    all_profiles = (_profile_from_row(r) for r in _read_rows(dataset_dir / PROFILES_FILE))
    profiles = {row.user_id: row for row in all_profiles if row.user_id in production_user_ids}

    all_events = (_event_from_row(r) for r in _read_rows(dataset_dir / EVENTS_FILE))
    events = {row.event_id: row for row in all_events if row.user_id in production_user_ids}
    rates = [_exchange_rate_from_row(r) for r in _read_rows(dataset_dir / EXCHANGE_RATES_FILE)]

    events_by_user: dict[str, list[FinancialEvent]] = defaultdict(list)
    for event in events.values():
        events_by_user[event.user_id].append(event)
    events_by_user_sorted = {
        user_id: tuple(sorted(items, key=lambda e: e.event_date)) for user_id, items in events_by_user.items()
    }

    payment_options_by_request: dict[str, list[PaymentOption]] = defaultdict(list)
    for row in _read_rows(dataset_dir / PAYMENT_OPTIONS_FILE):
        option = _payment_option_from_row(row)
        if option.request_id in requests:
            payment_options_by_request[option.request_id].append(option)

    messages_by_user: dict[str, list[Message]] = defaultdict(list)
    messages_by_request: dict[str, list[Message]] = defaultdict(list)
    for row in _read_rows(dataset_dir / MESSAGES_FILE):
        message = _message_from_row(row)
        if message.user_id not in production_user_ids:
            continue
        messages_by_user[message.user_id].append(message)
        if message.request_id is not None:
            messages_by_request[message.request_id].append(message)

    images_by_request: dict[str, list[ImageEvidence]] = defaultdict(list)
    for row in _read_rows(dataset_dir / IMAGES_FILE):
        image = _image_from_row(row, dataset_dir)
        if image.user_id not in production_user_ids:
            continue
        if image.request_id is not None:
            images_by_request[image.request_id].append(image)

    dataset = Dataset(
        profiles=profiles,
        requests=requests,
        events=events,
        events_by_user=events_by_user_sorted,
        payment_options_by_request={k: tuple(v) for k, v in payment_options_by_request.items()},
        messages_by_user={k: tuple(v) for k, v in messages_by_user.items()},
        messages_by_request={k: tuple(v) for k, v in messages_by_request.items()},
        images_by_request={k: tuple(v) for k, v in images_by_request.items()},
        exchange_rates=ExchangeRateTable.from_records(rates),
    )
    _validate_relationships(dataset)
    return dataset


def load_sample_request_user_ids(dataset_dir: str | Path) -> frozenset[str]:
    dataset_dir = Path(dataset_dir)
    rows = _read_rows(dataset_dir / SAMPLE_REQUESTS_FILE)
    return frozenset(require_id(row.get("user_id"), "user_id", f"{SAMPLE_REQUESTS_FILE}:{row.get('request_id')}") for row in rows)


def load_sample_request_ids(dataset_dir: str | Path) -> frozenset[str]:
    dataset_dir = Path(dataset_dir)
    rows = _read_rows(dataset_dir / SAMPLE_REQUESTS_FILE)
    return frozenset(require_id(row.get("request_id"), "request_id", SAMPLE_REQUESTS_FILE) for row in rows)
