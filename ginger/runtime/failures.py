from dataclasses import dataclass
from enum import Enum
from ginger.core.failure_spec import FailureId

@dataclass(frozen=True)
class RaisedFailure(Exception):
    fid: FailureId


class FailureStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class FailureEvent:
    """One occurrence, identified within its RuntimeContext by event_id.

    Immutable snapshots are updated through RuntimeContext, preserving the ID.
    RaisedFailure is the internal builtin signal; events are runtime history.
    """

    event_id: int
    failure_id: FailureId
    origin: str
    call_id: int
    status: FailureStatus = FailureStatus.UNRESOLVED
    caused_by: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "failure_id", FailureId(self.failure_id))
        object.__setattr__(self, "status", FailureStatus(self.status))


class FailureContractViolation(Exception):
    """Fatal boundary error, never a Ginger failure event eligible for resolve."""

    def __init__(self, failure_id, *, origin, violating_call_id,
                 violating_function_or_builtin, declared_contract,
                 boundary_kind, event_id=None):
        self.failure_id = FailureId(failure_id)
        self.origin = origin
        self.violating_call_id = violating_call_id
        self.violating_function_or_builtin = violating_function_or_builtin
        self.declared_contract = frozenset(declared_contract)
        self.boundary_kind = boundary_kind
        self.event_id = event_id
        declared = ', '.join(sorted(fid.value for fid in self.declared_contract))
        super().__init__(
            f'Failure contract violation; failure: {self.failure_id.value}; '
            f'origin: {origin}; boundary: {boundary_kind} {violating_function_or_builtin}; '
            f'call: {violating_call_id}; declared failures: {{{declared}}}; event: {event_id}')
