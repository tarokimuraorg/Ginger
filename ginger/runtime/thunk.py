from dataclasses import dataclass
from typing import Dict, Optional, Any
from ginger.ast import Expr

@dataclass
class ThunkValue:
    expr: Expr
    env: Dict[str, Any]
    outer: Optional[Dict[str, Any]]