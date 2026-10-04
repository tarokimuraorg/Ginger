from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Dict, Tuple
from .builtin import BUILTINS
from .errors import TypecheckError
from ginger.core.failure_spec import FailureId, FailureSet
from ginger.core.failure_contract import ConditionalFailure, FailureContract, substitute_contract
from .attrs import is_defined, get_attr
from ginger.core.prelude import prelude_items

from .ast import (
    Program,
    TypeRef,
    FailureSetDecl,
    GuaranteeDecl,
    TypeGroupDecl,
    RegisterDecl,
    ImplDecl,
    SigDecl,
    ConditionalFailureDecl,
    FuncDecl,
    VarDecl,
)


# =====================
# Symbols
# =====================

@dataclass(frozen=True)
class ResolvedCall:
    type_bindings: Dict[str, TypeRef]
    parameter_types: Tuple[TypeRef, ...]
    return_type: TypeRef
    implementation: str | None
    deferred_guarantee: str | None = None
    deferred_method: str | None = None


@dataclass(frozen=True)
class Symbols:

    guarantees: Dict[str, GuaranteeDecl]
    typegroups: Dict[str, set[str]]                  # group -> {"Int","Float",...}
    type_guarantees: Dict[str, set[str]]             # type -> {"Addable",...}
    sigs: Dict[str, SigDecl]
    sig_failures: Dict[str, FailureSet]
    sig_conditional_failures: Dict[str, frozenset[ConditionalFailure]]
    failuresets: Dict[str, FailureSet]
    sig_attrs: Dict[str, set[str]]
    funcs: Dict[str, FuncDecl]                       # name -> decl
    impls: Dict[Tuple[str, str, str], str]           # (Type, Guarantee, Method) -> builtin_id
    expression_types: Dict[int, TypeRef]  # Static, per-check expression annotations
    resolved_calls: Dict[int, ResolvedCall]  # Shared by effects and runtime; keyed by expression id.
    types: set[str]                                  # プリミティブ型


def _is_typevar(name: str) -> bool:
    # T, Uのような1文字大文字を型変数扱い
    return len(name) == 1 and name.isupper()

def _same_type(a: TypeRef, b: TypeRef) -> bool:
    """Compare normalized types, including recursive arguments and latent contracts."""
    if a.latent_failures != b.latent_failures:
        return False
    if a.latent_conditions != b.latent_conditions:
        return False
    if a.name != b.name:
        return False
    if len(a.args) != len(b.args):
        return False
    return all(_same_type(x, y) for x, y in zip(a.args, b.args))


def _build_failuresets(items) -> Dict[str, FailureSet]:
    declarations: Dict[str, FailureSetDecl] = {}
    reserved = {fid.value for fid in FailureId} | {"Never"}
    for item in items:
        if not isinstance(item, FailureSetDecl):
            continue
        if item.name in declarations:
            raise TypecheckError(f"duplicate failureset definition '{item.name}'")
        if item.name in reserved:
            raise TypecheckError(f"failureset name '{item.name}' conflicts with a failure name")
        declarations[item.name] = item

    resolved: Dict[str, FailureSet] = {}
    for name, decl in declarations.items():
        if not decl.members:
            raise TypecheckError(f"empty failureset is not supported: '{name}'")
        seen = set()
        members = set()
        for member in decl.members:
            if member in seen:
                raise TypecheckError(f"duplicate failure in failureset '{name}': '{member}'")
            seen.add(member)
            if member == "Never":
                raise TypecheckError(f"Never is not allowed in failureset '{name}'")
            if member in declarations:
                raise TypecheckError(
                    f"nested failureset is not supported in failureset '{name}': '{member}'"
                )
            try:
                members.add(FailureId(member))
            except ValueError:
                raise TypecheckError(
                    f"unknown failure in failureset '{name}': '{member}'"
                ) from None
        resolved[name] = frozenset(members)
    return resolved


def normalize_failures(names, failuresets, context):
    if len(set(names)) != len(names):
        raise TypecheckError(f"duplicate failure in {context}")
    if "Never" in names and len(names) > 1:
        raise TypecheckError(f"cannot combine 'Never' with other failures in {context}")
    expanded = set()
    for name in names:
        if name == "Never":
            continue
        if name in failuresets:
            expanded.update(failuresets[name])
        else:
            try:
                expanded.add(FailureId(name))
            except ValueError:
                raise TypecheckError(f"unknown failure '{name}' in {context}") from None
    return frozenset(expanded)


def normalize_types(value, failuresets):
    """Resolve source contracts separately from ordinary type arguments."""
    if isinstance(value, TypeRef):
        args = tuple(normalize_types(a, failuresets) for a in value.args)
        if value.name == "Thunk":
            if len(args) != 1 or (value.latent_failures is None and not value.failure_specs):
                raise TypecheckError("Thunk requires a result type and explicit failure contract")
            latent = value.latent_failures
            if latent is None:
                latent = normalize_failures(value.failure_specs, failuresets, "Thunk")
            return TypeRef("Thunk", args, latent_failures=latent,
                           latent_conditions=value.latent_conditions)
        return replace(value, args=args)
    if isinstance(value, list):
        return [normalize_types(v, failuresets) for v in value]
    if is_dataclass(value):
        return replace(value, **{f.name: normalize_types(getattr(value, f.name), failuresets)
                                 for f in fields(value)})
    return value


def build_symbols(prog: Program) -> Symbols:

    guarantees: Dict[str, GuaranteeDecl] = {}
    typegroups: Dict[str, set[str]] = {}
    type_guarantees: Dict[str, set[str]] = {}
    sigs: Dict[str, SigDecl] = {}
    sig_failures: Dict[str, FailureSet] = {}
    sig_conditional_failures: Dict[str, frozenset[ConditionalFailure]] = {}
    sig_attrs: Dict[str, set[str]] = {}
    funcs: Dict[str, FuncDecl] = {}
    impls: Dict[Tuple[str, str, str], str] = {}
    types: set[str] = set()

    items = prelude_items()
    items += list(prog.items)

    # Resolve named sets before sig normalization, including catalog sigs.
    failuresets = _build_failuresets(items)

    items = normalize_types(items, failuresets)
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

            # sem attr の制約を適用
            for a in attrs:
                ad = get_attr(a)
                if ad.require_return is not None and item.ret.name != ad.require_return:
                    raise TypecheckError(
                        f"@attr.{a} sig '{item.name}' must return {ad.require_return}"
                    )
                
            # --- builtin (SigDecl.builtin) ---
            b = getattr(item, "builtin", None)

            if b is not None:
                # 混線防止：requires と builtin は同時に持てない
                if item.requires:
                    raise TypecheckError(
                        f"sig '{item.name}' cannot have both requires and builtin (choose one dispatch style)"
                    )
                if b not in BUILTINS:
                    raise TypecheckError(f"unknown builtin '{b}' for sig '{item.name}'")
                
            # Keep concrete FailureSets independent of conditional clauses.
            fnames = list(getattr(item, "failures", []) or [])

            sig_failures[item.name] = normalize_failures(
                [f for f in fnames if isinstance(f, str)], failuresets, f"sig '{item.name}'")

        elif isinstance(item, FuncDecl):

            for attr in item.attrs:
                if not is_defined(attr):
                    raise TypecheckError(f"unknown attr '@attr.{attr}' on func '{item.name}'")

            if item.name in funcs:
                raise TypecheckError(f"duplicate func '{item.name}'")
            
            funcs[item.name] = item

            # func には必ず sig が必要
            if item.name not in sigs:
                raise TypecheckError(f"func '{item.name}' has no corresponding sig '{item.name}'")
            
            # func が sig と食い違っていないことを確認
            sig = sigs[item.name]

            # 引数数と各位置の型（型引数を含む）が一致することを確認
            if len(item.params) != len(sig.params) or any(
                not _same_type(p.typ, t) for p, t in zip(item.params, sig.params)
            ):
                raise TypecheckError(
                    f"func '{item.name}' parameter count or positional types do not match sig '{item.name}'"
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
        sig_conditional_failures=sig_conditional_failures,
        failuresets=failuresets,
        sig_attrs=sig_attrs,
        funcs=funcs,
        impls=impls,
        types=types,
        expression_types={},
        resolved_calls={},
    )

    _validate_catalog(syms)
    return syms

# =====================
# Catalog validation
# =====================
def _validate_catalog(syms: Symbols) -> None:
    # Validate conditions after all declarations and named sets are available.
    for name, sig in syms.sigs.items():
        variables = set()
        def collect(typ):
            if _is_typevar(typ.name):
                variables.add(typ.name)
            for arg in typ.args:
                collect(arg)
        for typ in [*sig.params, sig.ret]:
            collect(typ)
        clauses = set()
        for failure in sig.failures:
            if isinstance(failure, str):
                continue
            if not isinstance(failure, ConditionalFailureDecl):
                raise TypecheckError(f"invalid failure clause in sig '{name}'")
            if failure.failure_name == "Never":
                raise TypecheckError("conditional Never is not allowed")
            if failure.failure_name in syms.failuresets:
                raise TypecheckError("conditional failureset is not supported; use an individual FailureId")
            try:
                fid = FailureId(failure.failure_name)
            except ValueError:
                raise TypecheckError(f"unknown failure '{failure.failure_name}' in sig '{name}'") from None
            if failure.type_var not in variables:
                raise TypecheckError(f"unknown type variable '{failure.type_var}' in failure condition of sig '{name}'")
            if failure.guarantee_name not in syms.guarantees:
                raise TypecheckError(f"unknown guarantee '{failure.guarantee_name}' in failure condition of sig '{name}'")
            clause = ConditionalFailure(fid, failure.type_var, failure.guarantee_name)
            if clause in clauses:
                raise TypecheckError(f"duplicate conditional failure in sig '{name}'")
            if fid in syms.sig_failures[name]:
                raise TypecheckError(f"redundant conditional failure '{fid.value}' in sig '{name}'")
            if "Never" in sig.failures:
                raise TypecheckError(f"cannot combine 'Never' with other failures in sig '{name}'")
            clauses.add(clause)
        syms.sig_conditional_failures[name] = frozenset(clauses)

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


def signature_contract(syms: Symbols, name: str) -> FailureContract:
    return FailureContract(syms.sig_failures[name], syms.sig_conditional_failures[name])


def instantiated_signature_failures(syms: Symbols, name: str, bindings) -> FailureSet:
    return substitute_contract(signature_contract(syms, name), bindings,
                               syms.type_guarantees).concrete_failures()
