"""Evaluation values and public execution snapshots."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .failures import FailureEvent, FailureStatus
from .context import CallFrame, IncompleteStatement
from ginger.core.failure_spec import FailureId, FailureSet


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
class ContractViolationSnapshot:
    failure_id: FailureId
    origin: str
    violating_call_id: int
    violating_function_or_builtin: str
    declared_contract: FailureSet
    boundary_kind: str
    event_id: int | None

    @classmethod
    def from_violation(cls, violation):
        return cls(**{name: getattr(violation, name) for name in cls.__dataclass_fields__})

    def __str__(self):
        declared = ', '.join(sorted(fid.value for fid in self.declared_contract))
        return (f'Failure contract violation; failure: {self.failure_id.value}; '
                f'origin: {self.origin}; boundary: {self.boundary_kind} '
                f'{self.violating_function_or_builtin}; call: {self.violating_call_id}; '
                f'declared failures: {{{declared}}}; event: {self.event_id}')


@dataclass(frozen=True)
class ExecutionResult:
    """Execution snapshot; environment values are deliberately shallow-copied."""

    environment: Mapping[str, Any]
    failure_history: tuple[FailureEvent, ...]
    incomplete_statements: tuple[IncompleteStatement, ...] = ()
    call_frames: Mapping[int, CallFrame] = field(default_factory=dict)
    contract_violation: ContractViolationSnapshot | None = None

    def __post_init__(self):
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        object.__setattr__(self, "failure_history", tuple(self.failure_history))
        object.__setattr__(self, "incomplete_statements", tuple(self.incomplete_statements))
        object.__setattr__(self, "call_frames", MappingProxyType(dict(self.call_frames)))

    @classmethod
    def from_context(cls, environment, context):
        violation = context.last_contract_violation
        return cls(environment, context.failure_history, context.incomplete_statements,
                   context.call_frames,
                   ContractViolationSnapshot.from_violation(violation) if violation else None)

    @property
    def unresolved_events(self) -> tuple[FailureEvent, ...]:
        return tuple(event for event in self.failure_history
                     if event.status is FailureStatus.UNRESOLVED)
