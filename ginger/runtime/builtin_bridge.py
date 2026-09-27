"""Builtin-only event boundary; declared failures return NoValue."""

from typing import Any

from ginger.builtin import call_builtin
from ginger.core.failure_spec import FailureSet
from .context import RuntimeContext
from .failures import RaisedFailure
from .results import EvalResult, NoValue, Value


def invoke_builtin(implementation: str, args: list[Any], contract: FailureSet,
                   context: RuntimeContext, call_id: int) -> EvalResult:
    """Arguments are already values; catch only failures from this invocation."""
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
    return EvalResult(Value(value))
