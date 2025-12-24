from dataclasses import dataclass
from typing import Dict, Set, Tuple
from .builtin import BUILTINS
from .errors import TypecheckError
from .core.failure_spec import FailureId, failures, EMPTY_FAILURES, FailureSet

from .ast import (
    Program,
    GuaranteeDecl,
    TypeGroupDecl,
    RegisterDecl,
    ImplDecl,
    FuncDecl,
    VarDecl,
)


# =====================
# Symbols
# =====================

@dataclass(frozen=True)
class Symbols:

    guarantees: Dict[str, GuaranteeDecl]
    typegroups: Dict[str, Set[str]]                  # group -> {"Int","Float",...}
    type_guarantees: Dict[str, Set[str]]             # type -> {"Addable",...}
    funcs: Dict[str, FuncDecl]                       # name -> decl
    func_failures: Dict[str, FailureSet]
    func_attrs: Dict[str, Set[str]]
    impls: Dict[Tuple[str, str, str], str]           # (Type, Guarantee, Method) -> builtin_id
    types: set[str]                                  # プリミティブ型


def build_symbols(prog: Program) -> Symbols:

    guarantees: Dict[str, GuaranteeDecl] = {}
    typegroups: Dict[str, Set[str]] = {}
    type_guarantees: Dict[str, Set[str]] = {}
    funcs: Dict[str, FuncDecl] = {}
    func_failures: Dict[str, FailureSet] = {}
    func_attrs: Dict[str, Set[str]] = {}
    impls: Dict[Tuple[str, str, str], str] = {}
    types: Set[str] = set()

    for item in prog.items:

        if isinstance(item, GuaranteeDecl):
            if item.name in guarantees:
                raise TypecheckError(f"duplicate guarantee '{item.name}'")
            
            guarantees[item.name] = item
        
        if isinstance(item, TypeGroupDecl):

            if item.name in typegroups:
                raise TypecheckError(f"duplicate typegroup '{item.name}'")
            
            members = {t.name for t in item.members}
            typegroups[item.name] = members

            # typegroup名は型として存在
            types.add(item.name)

            # メンバー型も存在扱い
            types.update(members)

        elif isinstance(item, FuncDecl):

            if item.name in funcs:
                raise TypecheckError(f"duplicate func '{item.name}'")
            
            funcs[item.name] = item

            # attrs を保存（@noncritical など）
            func_attrs[item.name] = set(item.attrs)

            if "noncritical" in func_attrs[item.name] and item.ret.name != "Unit":
                raise TypecheckError(f"@noncritical function '{item.name} must return Unit")
            
            # failure は FuncDecl.failure: TypeRef (Never, PrintErr etc.)
            fname = item.failure.name if item.failure is not None else "Never"

            if fname == "Never":
                func_failures[item.name] = EMPTY_FAILURES
            else:
                try:
                    func_failures[item.name] = failures(FailureId(fname))
                except ValueError:
                    raise TypecheckError(
                        f"unknown failure '{fname}' in func '{item.name}'. "
                        f"use 'Never' or one of: {', '.join([f.value for f in FailureId])}"
                    )
                    

        elif isinstance(item, RegisterDecl):

            t = item.typ.name
            g = item.guarantee

            # guarantee の宣言がある前提（core注入 or catalog）
            gdecl = guarantees.get(g)

            if gdecl is None:
                raise TypecheckError(f"unknown guarantee '{g}'")

            # methods がある guarantee は register 禁止（Printable など）
            if len(gdecl.methods) > 0:
                raise TypecheckError(
                    f"'{g}' requires implementations; use impl, not register"
                )

            if g in type_guarantees.get(t, set()):
                raise TypecheckError(f"duplicate register: '{t}' guarantees '{g}'")
            
            type_guarantees.setdefault(t, set()).add(g)
            types.add(t)

        elif isinstance(item, ImplDecl):
            t = item.typ.name
            g = item.guarantee

            # impl is also a registration
            type_guarantees.setdefault(t, set()).add(g)

            for m in item.methods:
                key = (t, g, m.name)
                if key in impls:
                    raise TypecheckError(
                        f"duplicate impl for type '{t}', guarantee '{g}', method '{m.name}'"
                    )
                impls[key] = m.builtin
            
            # implされた型も存在
            types.add(t)

        elif isinstance(item, VarDecl):
            pass  # checked later

    syms = Symbols(
        guarantees=guarantees,
        typegroups=typegroups,
        type_guarantees=type_guarantees,
        funcs=funcs,
        func_failures=func_failures,
        func_attrs=func_attrs,
        impls=impls,
        types=types,
    )

    _validate_catalog(syms)
    return syms

# =====================
# Catalog validation
# =====================
def _validate_catalog(syms: Symbols) -> None:
    # 1) impl/register が参照する guarantee は存在するか
    for t, gs in syms.type_guarantees.items():
        for g in gs:
            if g not in syms.guarantees:
                raise TypecheckError(f"unknown guarantee '{g}' for type '{t}'")

    # 2) guarantee が要求する method が impl に揃ってるか
    for t, gs in syms.type_guarantees.items():
        for g in gs:
            gdecl = syms.guarantees[g]
            for msig in gdecl.methods:
                key = (t, g, msig.name)
                if key not in syms.impls:
                    raise TypecheckError(
                        f"type '{t}' guarantees '{g}' but missing impl for method '{msig.name}'"
                    )

    # 3) builtin 名が BUILTINS に存在するか
    for (t, g, m), builtin_name in syms.impls.items():
        if builtin_name not in BUILTINS:
            raise TypecheckError(
                f"unknown builtin '{builtin_name}' for impl {t} guarantees {g}.{m}"
            )