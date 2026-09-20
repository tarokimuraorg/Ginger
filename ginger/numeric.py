"""Concrete numeric widening, independent of equality and generic inference."""
from .ast import TypeRef


def can_widen(actual: TypeRef, expected: TypeRef) -> bool:
    """Only bare Ginger Int values widen to Float; constructors are invariant."""
    return actual == TypeRef("Int") and expected == TypeRef("Float")


def widen_value(value, expected: TypeRef | None):
    """Like toFloat, float() may round or propagate OverflowError.

    Python bool is not Ginger Int. No new Ginger failure is introduced.
    """
    if type(value) is int and can_widen(TypeRef("Int"), expected):
        return float(value)
    return value
