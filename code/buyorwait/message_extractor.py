from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Match

from .domain import Message
from .evidence import EvidenceFact, EvidenceSource, ExtractionMethod, FactProvenance, FactType, EvidenceStatus

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_AMOUNT_RE = re.compile(
    r"(?i)\b(?:IDR|INR|USD|EUR|ZAR|EUR)\s*[\d,]+(?:\.\d+)?|(?:[\d,]+(?:\.\d+)?)\s*(?:IDR|INR|USD|EUR|ZAR)"
)
_BARE_AMOUNT_RE = re.compile(r"\b([\d,]+(?:\.\d{2})?)\b")
_CURRENCY_RE = re.compile(r"\b(IDR|INR|USD|EUR|ZAR)\b")


def _extract_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def _extract_all_dates(text: str) -> list[date]:
    results = []
    for m in _DATE_RE.finditer(text):
        try:
            results.append(datetime.strptime(m.group(1), "%Y-%m-%d").date())
        except ValueError:
            pass
    return results


def _extract_currency(text: str) -> str | None:
    m = _CURRENCY_RE.search(text)
    return m.group(1) if m else None


def _extract_amount_with_currency(text: str) -> tuple[Decimal | None, str | None]:
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group(0)
        cur = _CURRENCY_RE.search(raw)
        nums = re.sub(r"[^\d.]", "", re.sub(r",", "", raw))
        if cur and nums:
            try:
                return Decimal(nums), cur.group(1)
            except InvalidOperation:
                pass
    return None, None


def _clean_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[^\d.]", "", raw.replace(",", "")))
    except InvalidOperation:
        return None


_PHISHING_PATTERNS = [
    re.compile(r"(?i)pay\s+(?:the\s+)?(?:release|processing)\s+(?:charge|fee)"),
    re.compile(r"(?i)congratulations.*cash\s+prize"),
    re.compile(r"(?i)selected\s+for\s+a\s+cash\s+prize"),
    re.compile(r"(?i)avoid\s+losing\s+the\s+claim"),
    re.compile(r"(?i)pay\s+now\s+to\s+(?:receive|unlock|claim)"),
    re.compile(r"(?i)wire\s+(?:transfer|money)\s+(?:to\s+receive|to\s+unlock)"),
    re.compile(r"(?i)send\s+(?:money|payment)\s+to\s+(?:receive|unlock|get)"),
]

_SOURCE_TRUST_MAP = {
    "employer": True,
    "bank": True,
    "financial_service": True,
    "merchant": True,
    "service_provider": True,
}


def _is_phishing_or_noise(text: str) -> bool:
    for pat in _PHISHING_PATTERNS:
        if pat.search(text):
            return True
    return False


def _is_prize_pending(text: str) -> bool:
    text_lc = text.lower()
    return (
        "prize" in text_lc
        and ("pending" in text_lc or "processing" in text_lc or "not been credited" in text_lc)
        and not _is_phishing_or_noise(text)
    )


def _is_investment_noise(text: str, source_type: str) -> bool:
    text_lc = text.lower()
    return source_type == "financial_service" and (
        "displayed" in text_lc or "market value" in text_lc or "unrealized" in text_lc or "portfolio" in text_lc
    )


def _is_internal_transfer(text: str) -> bool:
    text_lc = text.lower()
    return (
        "transfer between your" in text_lc
        or "matching debit and credit" in text_lc
        or "between your two accounts" in text_lc
    )


def _is_foreign_refund_processing(text: str) -> bool:
    text_lc = text.lower()
    return "foreign-currency refund" in text_lc or "refund is still processing" in text_lc


def _is_salary_raise(text: str) -> bool:
    text_lc = text.lower()
    return (
        ("naik menjadi" in text_lc or "has increased to" in text_lc or "increased to" in text_lc)
        and ("gaji" in text_lc or "salary" in text_lc or "pay" in text_lc)
    )


def _is_salary_cut(text: str) -> bool:
    text_lc = text.lower()
    return (
        "reduced to" in text_lc or "temporary monthly pay" in text_lc or "jumlah yang dikurangi" in text_lc
        or "sementara" in text_lc and "gaji" in text_lc
    )


def _is_salary_first(text: str) -> bool:
    text_lc = text.lower()
    return (
        "first salary" in text_lc
        or "gaji pertama" in text_lc
        or "first salary from the new employer" in text_lc
    )


def _is_salary_confirmed(text: str) -> bool:
    text_lc = text.lower()
    return (
        ("confirmed" in text_lc or "dikonfirmasi" in text_lc)
        and ("salary" in text_lc or "gaji" in text_lc or "pay" in text_lc)
        and not _is_salary_first(text_lc)
    )


def _is_salary_date_shift(text: str) -> bool:
    text_lc = text.lower()
    return (
        ("expected on" in text_lc or "diharapkan pada" in text_lc or "dijadwalkan" in text_lc)
        and ("salary" in text_lc or "gaji" in text_lc)
    )


def _is_income_terminated(text: str) -> bool:
    text_lc = text.lower()
    return (
        "employment has ended" in text_lc
        or "seasonal contract has ended" in text_lc
        or "telah berakhir" in text_lc
        or "no regular salary" in text_lc
        or "no off-season income" in text_lc
        or "income that has ended" in text_lc
    )


def _is_income_resumed(text: str) -> bool:
    text_lc = text.lower()
    return "resumes on" in text_lc or "kembali" in text_lc and ("gaji" in text_lc or "salary" in text_lc)


def _is_income_bonus_pending(text: str) -> bool:
    text_lc = text.lower()
    return (
        "bonus" in text_lc
        and ("pending" in text_lc or "subject to" in text_lc or "belum" in text_lc or "menunggu" in text_lc)
    )


def _is_refund_pending(text: str) -> bool:
    text_lc = text.lower()
    return (
        "refund has been initiated" in text_lc
        and ("not reached" in text_lc or "has not reached" in text_lc or "not yet" in text_lc or "belum" in text_lc)
    )


def _is_debit_failed(text: str) -> bool:
    text_lc = text.lower()
    return (
        "debit attempt failed" in text_lc
        or "previous debit" in text_lc and "failed" in text_lc
    )


def _is_charge_disputed(text: str) -> bool:
    text_lc = text.lower()
    return (
        "extra card charge" in text_lc and "being investigated" in text_lc
        or "tagihan kartu tambahan" in text_lc
    )


def _is_new_recurring_expense(text: str) -> bool:
    text_lc = text.lower()
    return (
        "new recurring" in text_lc
        or "recurring childcare" in text_lc
        or "a new recurring" in text_lc
    )


def _is_rent_increase(text: str) -> bool:
    text_lc = text.lower()
    return (
        "lease increases" in text_lc
        or "perpanjangan sewa" in text_lc
        or "menaikkan biaya" in text_lc
        or "renewed lease increases" in text_lc
    )


def _is_invoice_approved(text: str) -> bool:
    text_lc = text.lower()
    return "client approved an invoice" in text_lc or "invoice payment" in text_lc and "approved" in text_lc


def _is_salary_remaining_after_termination(text: str) -> bool:
    text_lc = text.lower()
    return (
        "remaining confirmed" in text_lc and "salary" in text_lc
        or "household employment record has ended" in text_lc and "remaining" in text_lc
    )


def _make_fact_id(message_id: str, fact_type: FactType, idx: int = 0) -> str:
    return f"fact_msg_{message_id}_{fact_type.value}_{idx}"


def extract_message_facts(
    message: Message,
    known_event_ids: frozenset[str],
    request_id: str | None = None,
) -> list[EvidenceFact]:
    text = message.message_text
    src = message.source_type
    uid = message.user_id
    mid = message.message_id
    req = request_id or message.request_id
    evt = message.related_event_id

    facts: list[EvidenceFact] = []

    if _is_phishing_or_noise(text):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.IRRELEVANT),
            fact_type=FactType.IRRELEVANT,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=None,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.CONFIRMED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Message flagged as phishing/unsupported solicitation; excluded from financial state.",
            is_trusted=False,
            ambiguity_note="Contains phishing patterns: pay-to-receive, prize claims with payment demands.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_investment_noise(text, src):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.IRRELEVANT),
            fact_type=FactType.IRRELEVANT,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.CONFIRMED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Message describes unrealized/displayed investment value — not spendable cash; excluded.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if _is_internal_transfer(text):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.TRANSFER_INTERNAL),
            fact_type=FactType.TRANSFER_INTERNAL,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.CONFIRMED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Matching debit+credit from internal account transfer — net cash effect is zero.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if _is_prize_pending(text):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.PRIZE_CLAIM_PENDING),
            fact_type=FactType.PRIZE_CLAIM_PENDING,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.UNRESOLVED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Prize claim reported as pending/processing — must NOT be counted as cash until settled.",
            is_trusted=True,
            ambiguity_note="Prize pending; do not treat as available income per challenge rules.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_charge_disputed(text):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.CHARGE_DISPUTED),
            fact_type=FactType.CHARGE_DISPUTED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.UNRESOLVED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Extra card charge is under investigation — treat as unresolved pending debit.",
            is_trusted=True,
            ambiguity_note="Outcome unknown; conservatively reserve funds until resolved.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_debit_failed(text):
        effective_date = _extract_date(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.DEBIT_FAILED_RETRY),
            fact_type=FactType.DEBIT_FAILED_RETRY,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.ESTIMATED,
            amount=None,
            currency=None,
            effective_date=effective_date,
            end_date=None,
            recurrence=None,
            category=None,
            description="Previous debit attempt failed; a retry is expected — reserve the debit amount.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if _is_refund_pending(text):
        amount, currency = _extract_amount_with_currency(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.REFUND_PENDING),
            fact_type=FactType.REFUND_PENDING,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.UNRESOLVED,
            amount=amount,
            currency=currency,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Refund initiated but not yet credited — do not count as available cash.",
            is_trusted=True,
            ambiguity_note="Pending credit; excluded until settled per challenge rules.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_foreign_refund_processing(text):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.FOREIGN_CURRENCY_REFUND_PROCESSING),
            fact_type=FactType.FOREIGN_CURRENCY_REFUND_PROCESSING,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.UNRESOLVED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Foreign-currency refund is still processing — exclude until credited.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if _is_income_terminated(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        if not amount and _is_salary_remaining_after_termination(text):
            amount, currency = _extract_amount_with_currency(text)
        remaining_note = None
        text_lc = text.lower()
        if "remaining confirmed" in text_lc or "household employment" in text_lc and "remaining" in text_lc:
            remaining_note = "Partial income termination; remaining salary still applies."
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.INCOME_TERMINATED),
            fact_type=FactType.INCOME_TERMINATED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=effective_date,
            recurrence=None,
            category="income",
            description="Income stream terminated; do not project this salary beyond the termination date.",
            is_trusted=True,
            ambiguity_note=remaining_note,
            raw_source_ref=mid,
        ))
        return facts

    if _is_salary_raise(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.SALARY_RAISE),
            fact_type=FactType.SALARY_RAISE,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence="monthly",
            category="income",
            description=f"Salary raise to {amount} {currency} effective {effective_date}.",
            is_trusted=True,
            ambiguity_note=None if amount else "Amount not found in message text.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_salary_cut(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.SALARY_CUT),
            fact_type=FactType.SALARY_CUT,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence="monthly",
            category="income",
            description=f"Temporary salary reduction to {amount} {currency}.",
            is_trusted=True,
            ambiguity_note=None if amount else "Amount not found; treat as unresolved.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_salary_first(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.SALARY_FIRST),
            fact_type=FactType.SALARY_FIRST,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence="monthly",
            category="income",
            description=f"First salary from new employer: {amount} {currency} on {effective_date}.",
            is_trusted=True,
            ambiguity_note=None if amount else "Amount not found.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_income_resumed(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        new_recurring = _is_new_recurring_expense(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.INCOME_RESUMED),
            fact_type=FactType.INCOME_RESUMED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence="monthly",
            category="income",
            description=f"Regular salary resumes: {amount} {currency} from {effective_date}.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        if new_recurring:
            facts.append(EvidenceFact(
                fact_id=_make_fact_id(mid, FactType.EXPENSE_NEW_RECURRING, 1),
                fact_type=FactType.EXPENSE_NEW_RECURRING,
                source_type=EvidenceSource.MESSAGE,
                source_id=mid,
                user_id=uid,
                request_id=req,
                event_id=evt,
                extraction_method=ExtractionMethod.DETERMINISTIC,
                provenance=FactProvenance.EXPLICITLY_STATED,
                status=EvidenceStatus.UNRESOLVED,
                amount=None,
                currency=None,
                effective_date=effective_date,
                end_date=None,
                recurrence="monthly",
                category="childcare",
                description="New recurring childcare payment announced — amount not stated; cannot be invented.",
                is_trusted=True,
                ambiguity_note="UNRESOLVED: amount explicitly missing; do not assume zero or any value.",
                raw_source_ref=mid,
            ))
        return facts

    if _is_income_bonus_pending(text):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.INCOME_BONUS_PENDING),
            fact_type=FactType.INCOME_BONUS_PENDING,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.UNRESOLVED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category="income",
            description="Quarterly bonus is pending final performance review — amount and date unconfirmed; do not count.",
            is_trusted=True,
            ambiguity_note="Do not include as income until settled.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_salary_date_shift(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.SALARY_DATE_SHIFT),
            fact_type=FactType.SALARY_DATE_SHIFT,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence="monthly",
            category="income",
            description=f"Salary payment date shifted; next credit expected on {effective_date}.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if _is_salary_confirmed(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.SALARY_CONFIRMED),
            fact_type=FactType.SALARY_CONFIRMED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence="monthly",
            category="income",
            description=f"Confirmed salary: {amount} {currency} on {effective_date}.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if _is_rent_increase(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        pct_m = re.search(r"(\d+)%", text)
        pct = pct_m.group(1) if pct_m else None
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.EXPENSE_RENT_INCREASE),
            fact_type=FactType.EXPENSE_RENT_INCREASE,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence="monthly",
            category="housing",
            description=f"Lease renewed with {pct or 'unknown'}% rent increase to {amount} {currency} from {effective_date}.",
            is_trusted=True,
            ambiguity_note=None if amount else "New amount not explicitly stated.",
            raw_source_ref=mid,
        ))
        return facts

    if _is_invoice_approved(text):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.INCOME_INVOICE_APPROVED),
            fact_type=FactType.INCOME_INVOICE_APPROVED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.ESTIMATED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence=None,
            category="income",
            description=f"Client approved invoice payment of {amount} {currency}, expected on {effective_date}.",
            is_trusted=True,
            ambiguity_note="Not yet credited; treat as pending income until settled.",
            raw_source_ref=mid,
        ))
        return facts

    text_lc = text.lower()

    if "investment sale" in text_lc or "proceeds from your investment sale" in text_lc or "penjualan investasi" in text_lc:
        amount, currency = _extract_amount_with_currency(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.REFUND_SETTLED),
            fact_type=FactType.REFUND_SETTLED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=_extract_date(text),
            end_date=None,
            recurrence=None,
            category="investments",
            description="Investment sale proceeds settled to cash account.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if "refund" in text_lc and ("confirmed" in text_lc or "credited" in text_lc):
        amount, currency = _extract_amount_with_currency(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.REFUND_SETTLED),
            fact_type=FactType.REFUND_SETTLED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=_extract_date(text),
            end_date=None,
            recurrence=None,
            category=None,
            description="Refund confirmed/settled to account.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if "pengembalian dana sudah diproses" in text_lc or ("sudah diproses" in text_lc and "belum" in text_lc):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.REFUND_PENDING),
            fact_type=FactType.REFUND_PENDING,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.UNRESOLVED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Refund processed (Bahasa Indonesia) but not yet credited; do not count as cash.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if "debit dan kredit" in text_lc and "transfer" in text_lc:
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.TRANSFER_INTERNAL),
            fact_type=FactType.TRANSFER_INTERNAL,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.CONFIRMED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Internal account transfer (Bahasa Indonesia) — net cash effect zero.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    if "minimum payment" in text_lc and "card" in text_lc:
        amount, currency = _extract_amount_with_currency(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.EXPENSE_AMOUNT),
            fact_type=FactType.EXPENSE_AMOUNT,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=_extract_date(text),
            end_date=None,
            recurrence=None,
            category="debt_payments",
            description="Minimum card payment(s) due — reserve as upcoming debit.",
            is_trusted=True,
            ambiguity_note="Multiple cards may be involved.",
            raw_source_ref=mid,
        ))
        return facts

    if "payout is still pending" in text_lc or ("payout" in text_lc and "pending" in text_lc):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.INCOME_INVOICE_APPROVED),
            fact_type=FactType.INCOME_INVOICE_APPROVED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.UNRESOLVED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category="income",
            description="Gig/freelance payout still pending — do not count until credited.",
            is_trusted=True,
            ambiguity_note="Pending income; treat as unresolved.",
            raw_source_ref=mid,
        ))
        return facts

    if "reimbursement" in text_lc and src == "employer":
        amount, currency = _extract_amount_with_currency(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.INCOME_INVOICE_APPROVED),
            fact_type=FactType.INCOME_INVOICE_APPROVED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.ESTIMATED,
            amount=amount,
            currency=currency,
            effective_date=_extract_date(text),
            end_date=None,
            recurrence=None,
            category="income",
            description="Employer reimbursement credit — pending until settled.",
            is_trusted=True,
            ambiguity_note="Not yet credited.",
            raw_source_ref=mid,
        ))
        return facts

    if (
        "regular salary for the next payroll" in text_lc
        or "regular salary for the next" in text_lc
        or ("gaji rutin" in text_lc and "berikutnya" in text_lc)
        or ("salary" in text_lc and "payroll includes" in text_lc)
        or ("regular salary" in text_lc and "next payroll" in text_lc)
    ):
        amount, currency = _extract_amount_with_currency(text)
        effective_date = _extract_date(text)
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.SALARY_CONFIRMED),
            fact_type=FactType.SALARY_CONFIRMED,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.EXPLICITLY_STATED,
            status=EvidenceStatus.CONFIRMED,
            amount=amount,
            currency=currency,
            effective_date=effective_date,
            end_date=None,
            recurrence="monthly",
            category="income",
            description=f"Confirmed regular salary for next payroll: {amount} {currency}.",
            is_trusted=True,
            ambiguity_note=None if amount else "Amount not extracted.",
            raw_source_ref=mid,
        ))
        return facts

    if "tagihan dikenakan dalam mata uang asing" in text_lc or ("mata uang asing" in text_lc and "tagihan" in text_lc):
        facts.append(EvidenceFact(
            fact_id=_make_fact_id(mid, FactType.FOREIGN_CURRENCY_REFUND_PROCESSING),
            fact_type=FactType.FOREIGN_CURRENCY_REFUND_PROCESSING,
            source_type=EvidenceSource.MESSAGE,
            source_id=mid,
            user_id=uid,
            request_id=req,
            event_id=evt,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            provenance=FactProvenance.DETERMINISTICALLY_EXTRACTED,
            status=EvidenceStatus.UNRESOLVED,
            amount=None,
            currency=None,
            effective_date=None,
            end_date=None,
            recurrence=None,
            category=None,
            description="Foreign currency charge/refund pending (Bahasa Indonesia) — home currency amount TBD.",
            is_trusted=True,
            ambiguity_note=None,
            raw_source_ref=mid,
        ))
        return facts

    return [EvidenceFact(
        fact_id=_make_fact_id(mid, FactType.UNRESOLVED),
        fact_type=FactType.UNRESOLVED,
        source_type=EvidenceSource.MESSAGE,
        source_id=mid,
        user_id=uid,
        request_id=req,
        event_id=evt,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        provenance=FactProvenance.UNRESOLVED,
        status=EvidenceStatus.UNRESOLVED,
        amount=None,
        currency=None,
        effective_date=_extract_date(text),
        end_date=None,
        recurrence=None,
        category=None,
        description="Message not matched by any deterministic pattern; requires AI extraction or manual review.",
        is_trusted=_SOURCE_TRUST_MAP.get(src, False),
        ambiguity_note="Unmatched message pattern.",
        raw_source_ref=mid,
    )]
