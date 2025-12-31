from dataclasses import dataclass
from ginger.core.failure_spec import FailureId

@dataclass(frozen=True)
class RaisedFailure(Exception):
    fid: FailureId