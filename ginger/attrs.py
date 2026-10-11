from dataclasses import dataclass
from typing import Dict, Optional

@dataclass(frozen=True)
class AttrDef:
    # semantic constraints
    require_return: Optional[str] = None

ATTRS: Dict[str, AttrDef] = {
    "io": AttrDef(),
}

def is_defined(attr_name: str) -> bool:
    return attr_name in ATTRS

def get_attr(attr_name: str) -> AttrDef:
    return ATTRS[attr_name]
