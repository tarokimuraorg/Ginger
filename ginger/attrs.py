from dataclasses import dataclass
from typing import Dict, Literal, Optional

AttrKind = Literal["meta", "sem"]

@dataclass(frozen=True)
class AttrDef:
    name: str
    kind: AttrKind
    doc: str = ""

    # semantic constraints
    require_return: Optional[str] = None

ATTRS: Dict[str, AttrDef] = {

    #meta (分類)
    "io": AttrDef(name="io", kind="meta", doc="I/O related functions"),
    
    #sem (意味)
    "handled": AttrDef(
        name="handled",
        kind="sem",
        doc="Must return Unit",
        require_return="Unit",

    ),
}

def is_defined(attr_name: str) -> bool:
    return attr_name in ATTRS

def get_attr(attr_name: str) -> AttrDef:
    return ATTRS[attr_name]