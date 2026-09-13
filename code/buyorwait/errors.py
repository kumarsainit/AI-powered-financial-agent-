from __future__ import annotations


class DataIntegrityError(ValueError):
    def __init__(self, message: str, row_id: str, field: str, raw_value: object) -> None:
        super().__init__(f"{row_id}: {field}: {message} (raw={raw_value!r})")
        self.row_id = row_id
        self.field = field
        self.raw_value = raw_value


class ExchangeRateNotFoundError(LookupError):
    pass


class UnknownRequestError(LookupError):
    pass
