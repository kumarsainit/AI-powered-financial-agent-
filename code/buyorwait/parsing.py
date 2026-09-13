from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Type, TypeVar

from .errors import DataIntegrityError

E = TypeVar("E", bound=Enum)


def _require(value: str | None, field: str, row_id: str) -> str:
    if value is None or value.strip() == "":
        raise DataIntegrityError("value is required", row_id, field, value)
    return value.strip()


def optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def parse_date(value: str | None, field: str, row_id: str) -> date:
    raw = _require(value, field, row_id)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DataIntegrityError("not a valid date", row_id, field, value) from exc


def parse_optional_date(value: str | None, field: str, row_id: str) -> date | None:
    raw = optional_str(value)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DataIntegrityError("not a valid date", row_id, field, value) from exc


def parse_datetime(value: str | None, field: str, row_id: str) -> datetime:
    raw = _require(value, field, row_id)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataIntegrityError("not a valid timestamp", row_id, field, value) from exc


def parse_decimal(value: str | None, field: str, row_id: str) -> Decimal:
    raw = _require(value, field, row_id)
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise DataIntegrityError("not a valid amount", row_id, field, value) from exc


def parse_optional_decimal(value: str | None, field: str, row_id: str) -> Decimal | None:
    raw = optional_str(value)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise DataIntegrityError("not a valid amount", row_id, field, value) from exc


def parse_int(value: str | None, field: str, row_id: str) -> int:
    raw = _require(value, field, row_id)
    try:
        return int(raw)
    except ValueError as exc:
        raise DataIntegrityError("not a valid integer", row_id, field, value) from exc


def parse_optional_int(value: str | None, field: str, row_id: str) -> int | None:
    raw = optional_str(value)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise DataIntegrityError("not a valid integer", row_id, field, value) from exc


def parse_bool(value: str | None, field: str, row_id: str) -> bool:
    raw = _require(value, field, row_id).lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise DataIntegrityError("not a valid boolean", row_id, field, value)


def parse_pipe_list(value: str | None) -> tuple[str, ...]:
    raw = optional_str(value)
    if raw is None:
        return ()
    return tuple(part for part in raw.split("|") if part)


def parse_enum(enum_cls: Type[E], value: str | None, field: str, row_id: str) -> E:
    raw = _require(value, field, row_id)
    try:
        return enum_cls(raw)
    except ValueError as exc:
        raise DataIntegrityError("unrecognized value", row_id, field, value) from exc


def parse_optional_enum(enum_cls: Type[E], value: str | None, field: str, row_id: str) -> E | None:
    raw = optional_str(value)
    if raw is None:
        return None
    try:
        return enum_cls(raw)
    except ValueError as exc:
        raise DataIntegrityError("unrecognized value", row_id, field, value) from exc


def parse_pipe_enum_list(enum_cls: Type[E], value: str | None, field: str, row_id: str) -> tuple[E, ...]:
    parts = parse_pipe_list(value)
    result = []
    for part in parts:
        try:
            result.append(enum_cls(part))
        except ValueError as exc:
            raise DataIntegrityError("unrecognized value in list", row_id, field, value) from exc
    return tuple(result)


def require_id(value: str | None, field: str, row_id: str) -> str:
    return _require(value, field, row_id)
