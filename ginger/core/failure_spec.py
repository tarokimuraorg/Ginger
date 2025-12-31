from enum import Enum
from typing import FrozenSet

class FailureId(str, Enum):
    PrintErr = "PrintErr"
    IOErr = "IOErr"
    TimeErr = "TimeErr"
    RandomErr = "RandomErr"
    UnexpecterErr = "UnexpectedErr"

# --- FailureSet (effect) ---
FailureSet = FrozenSet[FailureId]
EMPTY_FAILURES: FailureSet = frozenset()

def failures(*ids: FailureId) -> FailureSet:
    """Build a FailureSet from given ids."""
    return frozenset(ids)

def union_failures(*sets) -> FailureSet:
    """Union multipie FailureSets."""
    out = set()

    for i, s in enumerate(sets):
        if s is None:
            continue
        if isinstance(s, type):
            raise TypeError(f"union_failures arg[{i}] is TYPE: {s!r}")
        out.update(s)

    return frozenset(out)

    
def remove_failure(s: FailureSet, fid: FailureId) -> FailureSet:
    """Remove one failure id (for catch)."""
    if fid not in s:
        return s
    
    # frozenset supports set difference
    return frozenset(set(s) - {fid})

def contains_failure(s: FailureSet, fid: FailureId) -> bool:
    return fid in s