"""Execution bookkeeping only; no evaluation or failure-contract policy yet."""

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from ginger.core.failure_spec import EMPTY_FAILURES, FailureId, FailureSet
from .failures import FailureEvent, FailureStatus


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


class RuntimeContext:
    """Own IDs and ordered history for one execution.

    Public records are immutable snapshots. Read get_event/get_call again after
    an update. Resolution replaces a record under the same ID; it never deletes
    history. IDs are local to this context, not globally unique.
    """

    def __init__(self):
        self._next_event_id = 1
        self._next_call_id = 1
        self._events: dict[int, FailureEvent] = {}
        self._calls: dict[int, CallFrame] = {}

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
        event = FailureEvent(self._next_event_id, failure_id, origin, call_id)
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

    def resolve(self, event_id: int) -> FailureEvent:
        event = self.get_event(event_id)
        if event.status is FailureStatus.UNRESOLVED:
            event = replace(event, status=FailureStatus.RESOLVED)
            self._events[event_id] = event
            for call_id, frame in self._calls.items():
                if event_id in frame.pending_event_ids:
                    self._calls[call_id] = replace(frame, pending_event_ids=tuple(
                        pending for pending in frame.pending_event_ids if pending != event_id))
        return event
