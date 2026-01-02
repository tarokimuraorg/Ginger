from dataclasses import dataclass
from typing import Dict, Tuple
from collections import Counter
from .builtin import BUILTINS
from .errors import TypecheckError
from ginger.core.failure_spec import FailureId, failures, EMPTY_FAILURES, FailureSet
from .attrs import is_defined, get_attr
from ginger.core.prelude import prelude_items

from .ast import (
    Program,
    GuaranteeDecl,
    TypeGroupDecl,
    RegisterDecl,
    ImplDecl,
    SigDecl,
    FuncDecl,
    VarDecl,
)


# =====================
# Symbols
# =====================

@dataclass(frozen=True)
class Symbols:

    guarantees: Dict[str, GuaranteeDecl]
    typegroups: Dict[str, set[str]]                  # group -> {"Int","Float",...}
    type_guarantees: Dict[str, set[str]]             # type -> {"Addable",...}
    sigs: Dict[str, SigDecl]
    sig_failures: Dict[str, FailureSet]
    sig_attrs: Dict[str, set[str]]
    funcs: Dict[str, FuncDecl]                       # name -> decl
    # func_failures: Dict[str, FailureSet]
    # func_attrs: Dict[str, Set[str]]
    impls: Dict[Tuple[str, str, str], str]           # (Type, Guarantee, Method) -> builtin_id
    types: set[str]                                  # プリミティブ型


def _is_typevar(name: str) -> bool:
    # T, Uのような1文字大文字を型変数扱い
    return len(name) == 1 and name.isupper()

def _type_multiset_from_sig(sig: SigDecl) -> Counter:
    return Counter([t.name for t in sig.params])

def _type_multiset_from_func(func: FuncDecl) -> Counter:
    return Counter([p.typ.name for p in func.params])


def build_symbols(prog: Program) -> Symbols:

    guarantees: Dict[str, GuaranteeDecl] = {}
    typegroups: Dict[str, set[str]] = {}
    type_guarantees: Dict[str, set[str]] = {}
    sigs: Dict[str, SigDecl] = {}
    sig_failures: Dict[str, FailureSet] = {}
    sig_attrs: Dict[str, set[str]] = {}
    funcs: Dict[str, FuncDecl] = {}
    impls: Dict[Tuple[str, str, str], str] = {}
    types: set[str] = set()

    items = prelude_items()
    items += list(prog.items)

    for item in items:

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

        elif isinstance(item, SigDecl):

            if item.name in sigs:
                raise TypecheckError(f"duplicate sig '{item.name}'")
            
            sigs[item.name] = item

            # sigに現れた具象型を types に登録
            # 戻り値
            if not _is_typevar(item.ret.name):
                types.add(item.ret.name)

            # 引数
            for t in item.params:
                if not _is_typevar(t.name):
                    types.add(t.name)

            # attrs を保存（@attr.*）
            attrs = set(getattr(item, "attrs", []) or [])

            # 未知の attr を禁止
            for a in attrs:
                if not is_defined(a):
                    raise TypecheckError(f"unknown attr '@attr.{a}' on sig '{item.name}'")
                
            sig_attrs[item.name] = attrs

            # Catalog以外で @attr の付与を禁止
            """
            if attrs and getattr(item, "origin", "unknown") != "catalog":
                raise TypecheckError(f"@attr is only allowed in Catalog (func '{item.name}')")
            """
            
            # sem attr の制約を適用
            for a in attrs:
                ad = get_attr(a)
                if ad.require_return is not None and item.ret.name != ad.require_return:
                    raise TypecheckError(
                        f"@attr.{a} sig '{item.name}' must return {ad.require_return}"
                    )
                
            # failure は SigDecl.failures: list[str]
            fnames = list(getattr(item, "failures", []) or [])

            if "Never" in fnames and len(fnames) > 1:
                raise TypecheckError(f"cannot combine 'Never' with other failures in sig '{item.name}'")
            #fnames = [n for n in fnames if n != "Never"]

            if not fnames:
                sig_failures[item.name] = EMPTY_FAILURES
            else:
                try:
                    sig_failures[item.name] = failures(*[FailureId(n) for n in fnames])
                except ValueError:
                    candidates = ", ".join([f.value for f in FailureId])
                    raise TypecheckError(
                        f"unknown failure(s) '{', '.join(fnames)}' in sig '{item.name}'. "
                        f"use none (implicit Never) or one of: {candidates}"
                    )

        elif isinstance(item, FuncDecl):

            if item.name in funcs:
                raise TypecheckError(f"duplicate func '{item.name}'")
            
            funcs[item.name] = item

            # func には必ず sig が必要
            if item.name not in sigs:
                raise TypecheckError(f"func '{item.name}' has no corresponding sig '{item.name}'")
            
            # func が sig と食い違っていないことを確認
            sig = sigs[item.name]

            # sig と func の引数型は順不同で可
            # 型の種類と個数が一致していることのみを確認
            if _type_multiset_from_func(item) != _type_multiset_from_sig(sig):
                raise TypecheckError(
                    f"func '{item.name} parameter types do not match sig '{item.name}' (order-insensitive)'"
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
        sigs=sigs,
        sig_failures=sig_failures,
        sig_attrs=sig_attrs,
        funcs=funcs,
        #func_failures=func_failures,
        #func_attrs=func_attrs,
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