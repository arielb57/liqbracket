"""Exact number parsing and formatting."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction


def to_fraction(value: object) -> Fraction:
    """Convert a JSON-ish value to an exact Fraction.

    Floats are rejected: ``0.1`` as a binary float is not one tenth, and silently
    accepting it would defeat exact arithmetic. Pass decimal strings instead.
    """
    if isinstance(value, bool):
        raise TypeError("booleans are not numbers here")
    if isinstance(value, (int, Fraction)):
        return Fraction(value)
    if isinstance(value, Decimal):
        return Fraction(value)
    if isinstance(value, str):
        try:
            return Fraction(value.strip())
        except ValueError as exc:
            raise ValueError(f"not a number: {value!r}") from exc
    if isinstance(value, float):
        raise TypeError(f"float {value!r} is inexact; pass it as a string")
    raise TypeError(f"cannot interpret {value!r} as a number")


def fmt_decimal(value: Fraction, places: int = 8) -> str:
    """Round-half-even decimal rendering for human display."""
    quantum = Decimal(1).scaleb(-places)
    d = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(quantum)
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def fmt_exact(value: Fraction) -> str:
    return str(value)
