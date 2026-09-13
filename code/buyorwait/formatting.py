from __future__ import annotations

from datetime import date
from decimal import Decimal

CENT = Decimal("0.01")

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def is_integral(amount: Decimal) -> bool:
    return amount == amount.to_integral_value()


def format_plan_amount(amount: Decimal) -> str:
    if is_integral(amount):
        return str(int(amount))
    return f"{amount.quantize(CENT):f}"


def format_safe_amount(amount: Decimal) -> str:
    if is_integral(amount):
        return str(int(amount))
    quantized = amount.quantize(CENT)
    text = f"{quantized:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_money_words(amount: Decimal, currency: str) -> str:
    if is_integral(amount):
        body = f"{int(amount):,}"
    else:
        body = f"{amount.quantize(CENT):,.2f}"
    return f"{currency} {body}"


def format_iso_date(value: date) -> str:
    return value.isoformat()


def format_long_date(value: date) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def describe_item(description: str) -> str:
    cleaned = description.strip()
    if not cleaned:
        return "this expense"
    if cleaned[1:2].isupper():
        return cleaned
    return cleaned[0].lower() + cleaned[1:]
