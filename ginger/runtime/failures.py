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
    RaisedFailure remains the evaluator's exception protocol during Phase 2.
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
