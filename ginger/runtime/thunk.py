from dataclasses import dataclass
from typing import Dict, Optional, Any
from ginger.ast import Expr
from ginger.core.failure_spec import FailureId, FailureSet

@dataclass(frozen=True)
class ThunkValue:
    """Non-memoized lexical computation; captured function environments retain
    type bindings and a frame ID for substitution, not for failure attribution.
    Field bindings are frozen; environment contents and the AST are not.
    """
    expr: Expr
    env: Dict[str, Any]
    outer: Optional[Dict[str, Any]]
    potential_failure_contract: FailureSet

    def __post_init__(self):
        # Only the existing set representations are accepted, not arbitrary iterables.
        contract = self.potential_failure_contract
        if not isinstance(contract, (set, frozenset)):
            raise TypeError("potential_failure_contract must be a set or frozenset of FailureId")
        if any(not isinstance(fid, FailureId) for fid in contract):
            raise TypeError("potential_failure_contract elements must be FailureId")
        object.__setattr__(self, "potential_failure_contract", frozenset(contract))
