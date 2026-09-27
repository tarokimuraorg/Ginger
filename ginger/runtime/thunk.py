from dataclasses import dataclass
from typing import Dict, Optional, Any
from ginger.ast import Expr
from ginger.core.failure_spec import FailureSet

@dataclass
class ThunkValue:
    """Non-memoized lexical computation; runtime call identity is not captured."""
    expr: Expr
    env: Dict[str, Any]
    outer: Optional[Dict[str, Any]]
    potential_failure_contract: FailureSet