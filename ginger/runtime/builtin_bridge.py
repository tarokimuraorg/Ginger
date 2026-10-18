"""Builtin-only event boundary; declared failures return NoValue."""

from typing import Any

from ginger.ast import TypeRef
from ginger.extension import ThunkHandle
from ginger.typecheck import compatible
from ginger.builtin import call_builtin
from ginger.core.failure_spec import FailureSet
from .context import RuntimeContext
from .failures import RaisedFailure
from .results import EvalResult, NoValue, Value
from .value_contract import ValueContractViolation, validate_builtin_return


def invoke_builtin(implementation: str, args: list[Any], contract: FailureSet,
                   context: RuntimeContext, call_id: int, *, return_type: TypeRef,
                   parameter_types: tuple[TypeRef, ...] | None = None) -> EvalResult:
    """Invoke prepared values and register failures from this invocation only.

    Supplying concrete parameter_types selects the formal extension boundary.
    The evaluator has already validated every argument; omitted metadata keeps
    the low-level bridge's raw representation for internal callers.
    """
    if parameter_types is not None:
        args = [context._issue_thunk_handle(value, typ) if typ.name == 'Thunk' else value
                for value, typ in zip(args, parameter_types)]
    try:
        value = call_builtin(implementation, *args)
    except RaisedFailure as failure:
        if failure.fid not in contract:
            context.fail_contract(
                failure_id=failure.fid, origin=implementation,
                violating_call_id=call_id,
                violating_function_or_builtin=implementation,
                declared_contract=contract, boundary_kind="builtin")
        event = context.register_failure(failure.fid, origin=implementation, call_id=call_id)
        return EvalResult(NoValue(), (event.event_id,))
    try:
        if parameter_types is not None and return_type.name == 'Thunk':
            reason = None
            if not isinstance(value, ThunkHandle):
                reason = 'wrong_python_type'
            else:
                entry = context._resolve_thunk_handle(value)
                if entry is None:
                    reason = 'invalid_extension_handle'
                elif not compatible(entry.public_type, return_type):
                    reason = 'contract_mismatch'
            if reason is not None:
                raise ValueContractViolation(
                    expected_type=return_type, value=value, implementation=implementation,
                    reason=reason, call_id=call_id, boundary_kind='extension_return')
            value = entry.thunk
        validate_builtin_return(value, return_type, implementation=implementation, call_id=call_id)
    except ValueContractViolation as violation:
        context.last_value_contract_violation = violation
        raise
    return EvalResult(Value(value))
