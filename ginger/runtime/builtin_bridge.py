"""Builtin-only event boundary; declared failures return NoValue."""

from typing import Any

from ginger.ast import TypeRef
from ginger.builtin import call_builtin
from ginger.core.failure_spec import FailureSet
from .context import RuntimeContext
from .failures import RaisedFailure
from .results import EvalResult, NoValue, Value
from .value_contract import ValueContractViolation, validate_builtin_return


def invoke_builtin(implementation: str, args: list[Any], contract: FailureSet,
                   context: RuntimeContext, call_id: int, *, return_type: TypeRef) -> EvalResult:
    """Arguments are already values; register failures from this invocation only."""
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
        validate_builtin_return(value, return_type, implementation=implementation, call_id=call_id)
    except ValueContractViolation as violation:
        context.last_value_contract_violation = violation
        raise
    return EvalResult(Value(value))
