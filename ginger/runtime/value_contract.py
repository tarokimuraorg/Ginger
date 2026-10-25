"""Public runtime value contracts; open-world payloads remain opaque."""

import math
from dataclasses import dataclass

from ginger.ast import TypeRef
from .thunk import ThunkValue
from ginger.numeric import INT_MIN, INT_MAX, INT64_MIN, INT64_MAX


def safe_value_repr(value):
    """Bound diagnostics without executing user-defined repr or huge int conversion."""
    if type(value) is int:
        if value.bit_length() > 256:
            return f'<int: {value.bit_length()} bits>'
        return repr(value)
    if type(value) in (float, bool) or value is None:
        return repr(value)
    if type(value) is str:
        return repr(value[:80]) + ('...' if len(value) > 80 else '')
    if type(value) is tuple:
        # Only a shallow preview: arbitrary elements and recursive shapes are opaque.
        parts = [safe_value_repr(v) if type(v) in (int, float, bool, str) or v is None
                 else '<opaque>' for v in value[:3]]
        return '(' + ', '.join(parts) + (', ...' if len(value) > 3 else '') + ')'
    return '<opaque>'


@dataclass(frozen=True)
class ValueContractViolationSnapshot:
    expected_type: TypeRef
    actual_python_type: type
    actual_value_repr: str
    implementation: str
    reason: str
    violating_call_id: int
    boundary_kind: str = 'builtin'

    @classmethod
    def from_violation(cls, violation):
        return cls(**{name: getattr(violation, name) for name in cls.__dataclass_fields__})

    def __str__(self):
        return (f'Value contract violation; expected: {self.expected_type.name}; '
                f'actual Python type: {type.__getattribute__(self.actual_python_type, "__name__")}; '
                f'value: {self.actual_value_repr}; reason: {self.reason}; '
                f'boundary: {self.boundary_kind} {self.implementation}; '
                f'call: {self.violating_call_id}')


class ValueContractViolation(Exception):
    """Fatal representation error, with no FailureId or FailureEvent."""

    def __init__(self, *, expected_type, value, implementation, reason, call_id, boundary_kind="builtin"):
        self.expected_type = expected_type
        self.actual_python_type = type(value)
        self.actual_value_repr = safe_value_repr(value)
        self.implementation = implementation
        self.reason = reason
        self.violating_call_id = call_id
        self.boundary_kind = boundary_kind
        super().__init__(str(ValueContractViolationSnapshot.from_violation(self)))


def validate_runtime_value(value, return_type: TypeRef, *, implementation, call_id,
                           boundary_kind="builtin"):
    """Validate exact standard TypeRefs without converting or retaining the value."""
    reason = None
    if return_type.name == 'Thunk':
        if not isinstance(value, ThunkValue):
            reason = 'wrong_python_type'
        elif not value.potential_failure_contract <= return_type.latent_failures:
            reason = 'contract_mismatch'
    elif return_type in (TypeRef('Int'), TypeRef('Int64')):
        lower, upper = ((INT_MIN, INT_MAX) if return_type == TypeRef('Int')
                        else (INT64_MIN, INT64_MAX))
        if type(value) is not int:
            reason = 'wrong_python_type'
        elif not lower <= value <= upper:
            reason = 'out_of_range'
    elif return_type in (TypeRef('Float'), TypeRef('Bool'), TypeRef('Unit')):
        expected = {'Float': float, 'Bool': bool, 'Unit': type(None)}[return_type.name]
        if type(value) is not expected:
            reason = 'wrong_python_type'
        elif return_type == TypeRef('Float') and not math.isfinite(value):
            reason = 'non_finite'
    elif return_type in (TypeRef('Ordering'), TypeRef('Slope')):
        if type(value) is not tuple:
            reason = 'wrong_python_type'
        elif (len(value) != 2 or type(value[0]) is not str or
              type(value[1]) is not str or value[0] != return_type.name):
            reason = 'invalid_shape'
        elif value[1] not in ({'Left', 'Flat', 'Right'} if return_type.name == 'Ordering'
                              else {'Rising', 'Flat', 'Falling'}):
            reason = 'invalid_variant'
    if reason is not None:
        raise ValueContractViolation(expected_type=return_type, value=value,
                                     implementation=implementation, reason=reason,
                                     call_id=call_id, boundary_kind=boundary_kind)


def validate_builtin_return(value, return_type: TypeRef, *, implementation, call_id):
    """Compatibility entry point for the builtin bridge."""
    validate_runtime_value(value, return_type, implementation=implementation, call_id=call_id)
