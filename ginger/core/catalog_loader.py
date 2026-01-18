import json
from pathlib import Path
from typing import Any, Dict, List, Union

from ginger.ast import (
    GuaranteeDecl,
    FuncSig,
    Param,
    TypeRef,
    ImplDecl,
    ImplMethod,
    SigDecl,
    RequireGuarantees,
)

Json = Dict[str, Any]


def _type_ref(obj: Any) -> TypeRef:
    """
    Accepts either:
        - {"ref": "Int"}
        - "Int"     (optional convenience)
    """
    if isinstance(obj, str):
        return TypeRef(obj)
    if isinstance(obj, dict) and "ref" in obj and isinstance(obj["ref"], str):
        return TypeRef(obj["ref"])
    raise ValueError(f"Invalid type ref: {obj!r}")


def _param(obj: Any) -> Param:
    """
    Accepts:
        - {"name": "self", "type": {"ref": "Self"}}
        - {"name": "self", "typ": {"ref": "Self"}}  # tolerate current naming
    """
    if not isinstance(obj, dict):
        raise ValueError(f"Invalid param: {obj!r}")
    name = obj.get("name")
    if not isinstance(name, str):
        raise ValueError(f"Param.name must be str: {obj!r}")
    t = obj.get("type", obj.get("typ"))
    return Param(name, _type_ref(t))


def _require(obj: Any):
    """
    Currently supports:
        - {"kind": "guarantees", "type_var": "T", "guarantee": "Addable"}
    """
    if not isinstance(obj, dict):
        raise ValueError(f"Invalid require: {obj!r}")
    kind = obj.get("kind")
    if kind != "guarantees":
        raise ValueError(f"Unknown require.kind: {kind!r}")
    type_var = obj.get("type_var")
    guarantee = obj.get("guarantee")
    if not isinstance(type_var, str) or not isinstance(guarantee, str):
        raise ValueError(f"Invalid guarantees require: {obj!r}")
    return RequireGuarantees(type_var=type_var, guarantee_name=guarantee)


def load_core_catalog_json(path: Union[str, Path]) -> List[Any]:
    """
    Load catalog JSON and return a flat list of Ginger AST decl items
    (GuaranteeDecl, ImplDecl, SigDecl)
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Catalog JSON root must be an object")

    out: List[Any] = []

    # --- guarantees ---
    for g in data.get("guarantees", []):
        if not isinstance(g, dict):
            raise ValueError(f"Invalid guarantee: {g!r}")
        gname = g.get("name")
        if not isinstance(gname, str):
            raise ValueError(f"Guarantee.name must be str: {g!r}")

        methods: List[FuncSig] = []
        for m in g.get("methods", []):
            if not isinstance(m, dict):
                raise ValueError(f"Invalid guarantee method: {m!r}")
            mname = m.get("name")
            if not isinstance(mname, str):
                raise ValueError(f"FuncSig.name must be str: {m!r}")

            params = [_param(x) for x in m.get("params", [])]
            ret = _type_ref(m.get("ret"))
            methods.append(FuncSig(name=mname, params=params, ret=ret))

        out.append(GuaranteeDecl(name=gname, methods=methods))

    # --- impls ---
    for imp in data.get("impls", []):
        if not isinstance(imp, dict):
            raise ValueError(f"Invalid impl: {imp!r}")
        typ = _type_ref(imp.get("type", imp.get("typ")))
        guarantee = imp.get("guarantee")
        if not isinstance(guarantee, str):
            raise ValueError(f"Impl.guarantee must be str: {imp!r}")

        methods: List[ImplMethod] = []
        for m in imp.get("methods", []):
            if not isinstance(m, dict):
                raise ValueError(f"Invalid impl method: {m!r}")
            mname = m.get("name")
            builtin = m.get("builtin")
            if not isinstance(mname, str) or not isinstance(builtin, str):
                raise ValueError(f"ImplMethod fields invalid: {m!r}")
            methods.append(ImplMethod(name=mname, builtin=builtin))

        out.append(ImplDecl(typ=typ, guarantee=guarantee, methods=methods))

    # --- sigs ---
    for s in data.get("sigs", []):
        if not isinstance(s, dict):
            raise ValueError(f"Invalid sig: {s!r}")
        sname = s.get("name")
        if not isinstance(sname, str):
            raise ValueError(f"Sig.name must be str: {s!r}")

        params = [_type_ref(x) for x in s.get("params", [])]
        ret = _type_ref(s.get("ret"))
        requires = [_require(x) for x in s.get("requires", [])]
        failures = s.get("failures", [])
        attrs = s.get("attrs", [])
        builtin = s.get("builtin")
        if builtin is not None and not isinstance(builtin, str):
            raise ValueError(
                f"Sig '{sname}' is missing 'builtin': "
                "use null to indicate 'intentionally nobuiltin'"
            )

        out.append(
            SigDecl(
                name=sname,
                params=params,
                ret=ret,
                requires=requires,
                failures=failures,
                attrs=attrs,
                builtin=builtin,
            )
        )

    return out