"""Concrete numeric ranges and directional widening (constructors are invariant)."""
from .ast import TypeRef

INT_MAX = (1 << 53) - 1
INT_MIN = -INT_MAX
INT64_MAX = (1 << 63) - 1
INT64_MIN = -INT64_MAX


def can_widen(actual: TypeRef, expected: TypeRef) -> bool:
    return actual == TypeRef("Int") and expected in (TypeRef("Float"), TypeRef("Int64"))


def widen_value(value, actual: TypeRef, expected: TypeRef | None):
    """Use Ginger types, never infer integer identity from Python representation.

    Int -> Int64 preserves the Python int representation. Range enforcement for
    arithmetic results is performed by the managed integer builtins.
    """
    if can_widen(actual, expected) and expected == TypeRef("Float"):
        return float(value)
    return value
