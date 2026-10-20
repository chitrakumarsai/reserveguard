"""Exact conversion between internal integer minor units and Airwallex major-unit amounts."""

import pytest

from reserveguard.airwallex.money import from_major, to_major

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("cents", "currency", "expected"),
    [(255_000, "USD", "2550.00"), (5, "USD", "0.05"), (0, "USD", "0.00"), (1_500, "JPY", "1500")],
)
def test_to_major(cents: int, currency: str, expected: str) -> None:
    assert to_major(cents, currency) == expected


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        ("2550.00", "USD", 255_000),
        (2550, "USD", 255_000),
        (0.1, "USD", 10),
        ("0.05", "USD", 5),
        (1500, "JPY", 1_500),
    ],
)
def test_from_major(value: str | int | float, currency: str, expected: int) -> None:
    assert from_major(value, currency) == expected


def test_from_major_rejects_sub_minor_precision() -> None:
    with pytest.raises(ValueError, match="minor unit"):
        from_major("12.345", "USD")


def test_to_major_rejects_negative() -> None:
    with pytest.raises(ValueError):
        to_major(-1, "USD")


def test_round_trip() -> None:
    for cents in (1, 99, 100, 123_456_789):
        assert from_major(to_major(cents, "USD"), "USD") == cents
