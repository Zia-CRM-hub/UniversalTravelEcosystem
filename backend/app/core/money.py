"""Money is stored as integer minor units (paise, cents) to avoid float drift."""
from decimal import ROUND_HALF_UP, Decimal

_EXPONENT = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "UGX": 0,
             "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "TND": 3}


def exponent(currency: str) -> int:
    return _EXPONENT.get(currency.upper(), 2)


def to_minor(amount, currency: str) -> int:
    return int((Decimal(str(amount)) * 10 ** exponent(currency)).quantize(Decimal(1), ROUND_HALF_UP))


def to_major(minor: int, currency: str) -> Decimal:
    return Decimal(minor) / 10 ** exponent(currency)
