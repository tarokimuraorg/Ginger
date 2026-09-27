"""Execution bookkeeping and boundary contract validation."""

from dataclasses import dataclass, replace
from contextvars import ContextVar
from types import MappingProxyType
from typing import Mapping

from ginger.core.failure_spec import EMPTY_FAILURES, FailureId, FailureSet
from .failures import FailureEvent, FailureStatus, FailureContractViolation


@dataclass(frozen=True)
class CallFrame:
    """Call state, separate from the evaluator's variable/type environment."""

    call_id: int
    parent_call_id: int | None
    function_name: str
    declared_failure_contract: FailureSet = EMPTY_FAILURES
    pending_event_ids: tuple[int, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "declared_failure_contract", frozenset(self.declared_failure_contract))
        object.__setattr__(self, "pending_event_ids", tuple(self.pending_event_ids))


@dataclass(frozen=True)
class IncompleteStatement:
    call_id: int
    scope: str
    statement_index: int
    statement_kind: str
    related_event_ids: tuple[int, ...]


class RuntimeContext:
    """Own IDs and ordered history for one execution.

    Public records are immutable snapshots. Read get_event/get_call again after
    an update. Resolution replaces a record under the same ID; it never deletes
    history. IDs are local to this context, not globally unique.
    """

    def __init__(self):
        self.last_contract_violation: FailureContractViolation | None = None
        self._next_event_id = 1
        self._next_call_id = 1
        self._events: dict[int, FailureEvent] = {}
        self._calls: dict[int, CallFrame] = {}
        self._incomplete_statements: list[IncompleteStatement] = []
        self._current_call: ContextVar[int | None] = ContextVar("ginger_current_call", default=None)
        self._handling_event: ContextVar[int | None] = ContextVar("ginger_handling_event", default=None)

    @property
    def current_call_id(self) -> int | None:
        return self._current_call.get()

    def unresolved_pending(self, call_id: int) -> tuple[int, ...]:
        return tuple(event_id for event_id in self.get_call(call_id).pending_event_ids
                     if self.get_event(event_id).status is FailureStatus.UNRESOLVED)

    def call(self, function_name: str, declared_failure_contract: FailureSet = EMPTY_FAILURES):
        """Activate a retained frame and propagate unresolved references on exit."""
        return _CallScope(self, function_name, declared_failure_contract)

    @property
    def incomplete_statements(self) -> tuple[IncompleteStatement, ...]:
        return tuple(self._incomplete_statements)

    def record_incomplete(self, call_id: int, scope: str, index: int,
                          kind: str, event_ids: tuple[int, ...]) -> None:
        self.get_call(call_id)
        if not event_ids:
            raise ValueError("incomplete statement requires a failure cause")
        for event_id in event_ids:
            self.get_event(event_id)
        self._incomplete_statements.append(IncompleteStatement(
            call_id, scope, index, kind, tuple(event_ids)))

    @property
    def next_event_id(self) -> int:
        return self._next_event_id

    @property
    def next_call_id(self) -> int:
        return self._next_call_id

    @property
    def failure_history(self) -> tuple[FailureEvent, ...]:
        return tuple(self._events.values())

    @property
    def call_frames(self) -> Mapping[int, CallFrame]:
        return MappingProxyType(self._calls)

    def get_event(self, event_id: int) -> FailureEvent:
        return self._events[event_id]

    def get_call(self, call_id: int) -> CallFrame:
        return self._calls[call_id]

    def create_call(self, function_name: str, *, parent_call_id: int | None = None,
                    declared_failure_contract: FailureSet = EMPTY_FAILURES) -> CallFrame:
        if parent_call_id is not None:
            self.get_call(parent_call_id)
        frame = CallFrame(self._next_call_id, parent_call_id, function_name,
                          frozenset(declared_failure_contract))
        self._calls[frame.call_id] = frame
        self._next_call_id += 1
        return frame

    def register_failure(self, failure_id: FailureId, *, origin: str,
                         call_id: int) -> FailureEvent:
        self.get_call(call_id)
        event = FailureEvent(self._next_event_id, failure_id, origin, call_id,
                             caused_by=self._handling_event.get())
        self._events[event.event_id] = event
        self._next_event_id += 1
        self.add_pending(call_id, event.event_id)
        return event

    def add_pending(self, call_id: int, event_id: int) -> None:
        """Reference an existing event, without copying or re-registering it.

        This is a bookkeeping primitive, not automatic function propagation.
        """
        frame = self.get_call(call_id)
        event = self.get_event(event_id)
        if event.status is FailureStatus.RESOLVED:
            raise ValueError("a resolved event cannot be pending")
        if event_id not in frame.pending_event_ids:
            self._calls[call_id] = replace(
                frame, pending_event_ids=frame.pending_event_ids + (event_id,))

    def fail_contract(self, **details):
        violation = FailureContractViolation(**details)
        self.last_contract_violation = violation
        raise violation

    def validate_function_exit(self, call_id: int) -> None:
        """Check only events leaving this function, not root or resolved history."""
        frame = self.get_call(call_id)
        for event_id in self.unresolved_pending(call_id):
            event = self.get_event(event_id)
            if event.failure_id not in frame.declared_failure_contract:
                self.fail_contract(
                    failure_id=event.failure_id, origin=event.origin,
                    violating_call_id=call_id,
                    violating_function_or_builtin=frame.function_name,
                    declared_contract=frame.declared_failure_contract,
                    boundary_kind="function", event_id=event_id)

    def resolve(self, event_id: int) -> FailureEvent:
        event = self.get_event(event_id)
        if event.status is FailureStatus.UNRESOLVED:
            event = replace(event, status=FailureStatus.RESOLVED)
            self._events[event_id] = event
            # Keep frame references as propagation history. Live pending is
            # obtained through unresolved_pending, using event status as truth.
        return event

    def run_handler(self, event_id: int, handler):
        """Resolve only after nonfatal completion, including NoValue."""
        self.get_event(event_id)
        token = self._handling_event.set(event_id)
        try:
            result = handler()
        finally:
            self._handling_event.reset(token)
        self.resolve(event_id)
        return result


class _CallScope:
    """Class-based scope: do not mutate frozen RaisedFailure traceback fields."""

    def __init__(self, context, name, contract):
        self.context, self.name, self.contract = context, name, contract

    def __enter__(self):
        self.parent = self.context.current_call_id
        self.frame = self.context.create_call(self.name, parent_call_id=self.parent,
                                              declared_failure_contract=self.contract)
        self.token = self.context._current_call.set(self.frame.call_id)
        return self.frame

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.parent is not None and not isinstance(exc, FailureContractViolation):
                for event_id in self.context.unresolved_pending(self.frame.call_id):
                    self.context.add_pending(self.parent, event_id)
        finally:
            self.context._current_call.reset(self.token)
        return False
