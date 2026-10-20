"""Exact conversion between internal minor units (int) and Airwallex major-unit amounts.

Airwallex amounts are major units (``100`` means $100). Conversion goes through
Decimal so no binary-float rounding can creep into money paths.
"""

from decimal import Decimal

# ISO 4217 currencies without a minor unit; everything else here is assumed to have 2.
_ZERO_DECIMAL = frozenset({"JPY", "KRW", "VND", "IDR", "CLP", "ISK", "HUF", "TWD", "UGX"})


def minor_digits(currency: str) -> int:
    return 0 if currency.upper() in _ZERO_DECIMAL else 2


def to_major(minor: int, currency: str) -> str:
    """Minor units -> major-unit string, e.g. 255000 USD -> "2550.00"."""
    if minor < 0:
        raise ValueError(f"amount must be >= 0, got {minor}")
    digits = minor_digits(currency)
    return f"{Decimal(minor).scaleb(-digits):.{digits}f}"


def from_major(value: str | int | float, currency: str) -> int:
    """Major units from the API -> minor units. Rejects sub-minor-unit precision."""
    scaled = Decimal(str(value)).scaleb(minor_digits(currency))
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{value} {currency} is not a whole number of minor units")
    return int(scaled)
