"""Phase 3 builtin-only event boundary and temporary legacy control adapter."""

from typing import Any

from ginger.builtin import call_builtin
from ginger.core.failure_spec import FailureId, FailureSet
from .context import RuntimeContext
from .failures import RaisedFailure
from .results import EvalResult, NoValue, Value


def _call_with_legacy_failures(implementation: str, args: list[Any]) -> Any:
    # Preserve the evaluator's existing division mapping. Other Python errors
    # are not Ginger failures and must escape without being recorded as events.
    try:
        return call_builtin(implementation, *args)
    except ZeroDivisionError:
        raise RaisedFailure(FailureId.DivideByZero)


def invoke_builtin(implementation: str, args: list[Any], contract: FailureSet,
                   context: RuntimeContext, call_id: int) -> EvalResult:
    """Arguments are already values; catch only failures from this invocation."""
    try:
        value = _call_with_legacy_failures(implementation, args)
    except RaisedFailure as failure:
        if failure.fid not in contract:
            # Phase 7 will introduce contract violations. Do not legitimize an
            # undeclared failure by registering a normal event in Phase 3.
            raise
        event = context.register_failure(failure.fid, origin=implementation, call_id=call_id)
        return EvalResult(NoValue(), (event.event_id,))
    return EvalResult(Value(value))


def legacy_value(result: EvalResult, context: RuntimeContext) -> Any:
    """Single compatibility boundary, to be replaced by Phase 4 evaluation.

    NoValue never enters an environment or a parent expression. This bridge
    produces exactly one event per failed builtin; rethrowing does not register.
    """
    if isinstance(result.value_result, Value):
        return result.value_result.value
    if len(result.related_event_ids) != 1:
        raise ValueError("builtin NoValue requires exactly one failure event")
    event = context.get_event(result.related_event_ids[0])
    raise RaisedFailure(event.failure_id)
