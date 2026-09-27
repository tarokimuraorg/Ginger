"""Future evaluation/result types; the current evaluator API is unchanged."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .failures import FailureEvent, FailureStatus


@dataclass(frozen=True)
class Value:
    """A successfully produced value, including None for Ginger Unit."""

    value: Any


@dataclass(frozen=True)
class NoValue:
    """An evaluation that did not produce a value; never a Ginger value."""


@dataclass(frozen=True)
class EvalResult:
    value_result: Value | NoValue
    related_event_ids: tuple[int, ...] = ()

    def __post_init__(self):
        if not isinstance(self.value_result, (Value, NoValue)):
            raise TypeError("value_result must be Value or NoValue")
        object.__setattr__(self, "related_event_ids", tuple(self.related_event_ids))


@dataclass(frozen=True)
class ExecutionResult:
    """Snapshot of records and environment bindings, not a deep copy of values.

    incomplete_statements holds descriptive origins for now. Evaluator-specific
    statement records and source locations can be introduced when connected.
    """

    environment: Mapping[str, Any]
    failure_history: tuple[FailureEvent, ...]
    incomplete_statements: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        object.__setattr__(self, "failure_history", tuple(self.failure_history))
        object.__setattr__(self, "incomplete_statements", tuple(self.incomplete_statements))

    @property
    def unresolved_events(self) -> tuple[FailureEvent, ...]:
        return tuple(event for event in self.failure_history
                     if event.status is FailureStatus.UNRESOLVED)
