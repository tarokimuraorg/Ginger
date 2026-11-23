from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Dict, Tuple
from .builtin import BUILTINS
from .type_identity import TypeIdentityKey, type_identity_key
from .errors import TypecheckError
from ginger.core.failure_spec import FailureId, FailureSet
from ginger.core.failure_contract import (
    ConditionalFailure, FailureContract, substitute_contract, union_contracts,
)
from .attrs import ATTRS
from ginger.core.prelude import prelude_items

from .ast import (
    Program,
    TypeRef,
    FailureSetDecl,
    FailureSetRef,
    GuaranteeDecl,
    TypeGroupDecl,
    RegisterDecl,
    ImplDecl,
    SigDecl,
    RequireGuarantees,
    RequireIn,
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
    comparison_projection: str | None = None


@dataclass(frozen=True)
class Symbols:

    guarantees: Dict[str, GuaranteeDecl]
    typegroups: Dict[str, set[TypeIdentityKey]]      # group -> concrete type identities
    type_guarantees: Dict[TypeIdentityKey, set[str]]     # concrete type identity -> guarantees
    sigs: Dict[str, SigDecl]
    sig_failures: Dict[str, FailureSet]
    sig_conditional_failures: Dict[str, frozenset[ConditionalFailure]]
    failuresets: "FailureSets"
    funcs: Dict[str, FuncDecl]                       # name -> decl
    impls: Dict[Tuple[TypeIdentityKey, str, str], str]           # (Type, Guarantee, Method) -> builtin_id
    binding_types: Dict[int, TypeRef]  # Checked assignment targets, independent of Cell metadata
    expression_types: Dict[int, TypeRef]  # Static, per-check expression annotations
    resolved_calls: Dict[int, ResolvedCall]  # Shared by effects and runtime; keyed by expression id.


def _is_typevar(name: str) -> bool:
    # T, Uのような1文字大文字を型変数扱い
    return len(name) == 1 and name.isupper()

def _validate_concrete_identity(typ: TypeRef, position: str) -> None:
    """Validate normalized registration targets, without closing the type world."""
    if _is_typevar(typ.name):
        raise TypecheckError(
            f"symbolic type variable '{typ.name}' is not allowed in {position}"
        )
    if typ.name == "Self":
        raise TypecheckError(
            f"contextual placeholder 'Self' is not allowed in {position}"
        )
    for arg in typ.args:
        _validate_concrete_identity(arg, position)

def _collect_inferable_param_typevars(sig: SigDecl) -> set[str]:
    """Collect current argument-inference sources, excluding returns and contracts."""
    variables: set[str] = set()

    def collect(typ: TypeRef) -> None:
        if _is_typevar(typ.name):
            variables.add(typ.name)
            # Like collect_type_evidence, bind the whole type at a variable.
            return
        for arg in typ.args:
            collect(arg)

    for typ in sig.params:
        collect(typ)
    return variables

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


def _project_method_type(typ: TypeRef, required_type_var: str) -> TypeRef:
    """Project the canonical method contract into its public sig's namespace."""
    return replace(
        typ,
        name=required_type_var if typ.name == "Self" else typ.name,
        args=tuple(_project_method_type(arg, required_type_var) for arg in typ.args),
    )


@dataclass(frozen=True)
class GenericFailureSet:
    type_param: str
    contract: FailureContract


class FailureSets(dict):
    """Named definitions plus the complete membership environment for expansion."""
    def __init__(self, typegroups):
        super().__init__()
        self.typegroups = typegroups


def _build_failuresets(items):
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

    # Collect membership before expansion; source order must not affect contracts.
    groups = {}
    for item in items:
        if isinstance(item, TypeGroupDecl):
            for member in item.members:
                _validate_concrete_identity(member, "typegroup member")
            if item.name in groups:
                raise TypecheckError(f"duplicate typegroup '{item.name}'")
            members = {type_identity_key(t) for t in item.members}
            if len(members) != len(item.members):
                raise TypecheckError(f"duplicate register in typegroup '{item.name}'")
            groups[item.name] = members
    for item in items:
        if isinstance(item, RegisterDecl) and item.guarantee in groups:
            _validate_concrete_identity(item.typ, "register target")
            if item.typ.name in groups:
                raise TypecheckError(f"typegroup '{item.typ.name}' is not a concrete register target")
            key = type_identity_key(item.typ)
            if key in groups[item.guarantee]:
                raise TypecheckError(f"duplicate register in typegroup '{item.guarantee}': '{key}'")
            groups[item.guarantee].add(key)
    resolved = FailureSets(groups)
    for name, decl in declarations.items():
        if decl.type_param is not None and not _is_typevar(decl.type_param):
            raise TypecheckError(f"invalid failureset type parameter '{decl.type_param}'")
        if not decl.members:
            raise TypecheckError(f"empty failureset is not supported: '{name}'")
        seen = set()
        members = set()
        conditions = set()
        for member in decl.members:
            if member in seen:
                raise TypecheckError(f"duplicate failure in failureset '{name}': '{member}'")
            seen.add(member)
            condition = member if isinstance(member, ConditionalFailureDecl) else None
            if condition:
                if condition.type_var != decl.type_param:
                    raise TypecheckError(f"unknown type variable '{condition.type_var}' in failureset '{name}'")
                if condition.group_name not in groups:
                    raise TypecheckError(f"unknown typegroup '{condition.group_name}' in failureset '{name}'")
                member = condition.failure_name
            if isinstance(member, FailureSetRef):
                raise TypecheckError(f"nested failureset is not supported in failureset '{name}'")
            if member == "Never":
                raise TypecheckError(f"Never is not allowed in failureset '{name}'")
            if member in declarations:
                raise TypecheckError(
                    f"nested failureset is not supported in failureset '{name}': '{member}'"
                )
            try:
                fid = FailureId(member)
                if condition:
                    conditions.add(ConditionalFailure(fid, condition.type_var, condition.group_name))
                else:
                    members.add(fid)
            except ValueError:
                raise TypecheckError(
                    f"unknown failure in failureset '{name}': '{member}'"
                ) from None
        if any(c.failure_id in members for c in conditions):
            raise TypecheckError(f"redundant conditional failure in failureset '{name}'")
        contract = FailureContract(frozenset(members), frozenset(conditions))
        resolved[name] = (GenericFailureSet(decl.type_param, contract)
                          if decl.type_param else contract.unconditional)
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
            if isinstance(failuresets[name], GenericFailureSet):
                raise TypecheckError(f"failureset '{name}' requires one type argument")
            expanded.update(failuresets[name])
        else:
            try:
                expanded.add(FailureId(name))
            except ValueError:
                raise TypecheckError(f"unknown failure '{name}' in {context}") from None
    return frozenset(expanded)


def _type_variables(typ):
    return ({typ.name} if _is_typevar(typ.name) else set()).union(
        *(_type_variables(arg) for arg in typ.args))


def expand_failure_reference(ref, failuresets, variables):
    definition = failuresets.get(ref.name)
    if definition is None:
        raise TypecheckError(f"unknown failureset '{ref.name}'")
    if not isinstance(definition, GenericFailureSet):
        raise TypecheckError(f"failureset '{ref.name}' does not accept type arguments")
    unknown = _type_variables(ref.argument) - variables
    if unknown:
        raise TypecheckError(f"unknown type variable '{sorted(unknown)[0]}' in failureset reference")
    argument = normalize_types(ref.argument, failuresets, variables)
    return substitute_contract(definition.contract, {definition.type_param: argument},
                               failuresets.typegroups)


def normalize_types(value, failuresets, variables=frozenset()):
    """Expand named contracts into the existing symbolic representation in scope."""
    if isinstance(value, FailureSetDecl):
        return value
    if isinstance(value, (SigDecl, FuncDecl)):
        types = ([*value.params, value.ret] if isinstance(value, SigDecl)
                 else [p.typ for p in value.params])
        variables = set().union(*(_type_variables(t) for t in types))
    if isinstance(value, SigDecl):
        references = [f for f in value.failures if isinstance(f, FailureSetRef)]
        if len(set(references)) != len(references):
            raise TypecheckError(f"duplicate failure in sig '{value.name}'")
        if references and "Never" in value.failures:
            raise TypecheckError("cannot combine 'Never' with other failures")
        expanded = [f for f in value.failures if not isinstance(f, FailureSetRef)]
        for ref in references:
            contract = expand_failure_reference(ref, failuresets, variables)
            for entry in [*(fid.value for fid in contract.unconditional),
                          *(ConditionalFailureDecl(c.failure_id.value, c.type_var, c.group_name)
                            for c in contract.conditional)]:
                if entry not in expanded:
                    expanded.append(entry)
        value = replace(value, failures=expanded)
    if isinstance(value, TypeRef):
        args = tuple(normalize_types(a, failuresets, variables) for a in value.args)
        if value.name == "Thunk":
            if len(args) != 1:
                raise TypecheckError("Thunk expects one type argument; write failure contract as Thunk[T]<...>")
            if value.latent_failures is None and not value.failure_specs:
                raise TypecheckError("Thunk requires a result type and explicit failure contract")
            latent = FailureContract(value.latent_failures or frozenset(), value.latent_conditions)
            if value.latent_failures is None:
                specs = value.failure_specs
                if len(set(specs)) != len(specs):
                    raise TypecheckError("duplicate failure in Thunk")
                if "Never" in specs and len(specs) > 1:
                    raise TypecheckError("cannot combine 'Never' with other failures in Thunk")
                latent = union_contracts(
                    FailureContract(normalize_failures([f for f in specs if isinstance(f, str)],
                                                       failuresets, "Thunk")),
                    *(expand_failure_reference(f, failuresets, variables)
                      for f in specs if isinstance(f, FailureSetRef)))
            return TypeRef("Thunk", args, latent_failures=latent.unconditional,
                           latent_conditions=latent.conditional)
        if value.failure_specs is not None:
            raise TypecheckError("failure contract postfix is allowed only on Thunk")
        return replace(value, args=args)
    if isinstance(value, list):
        return [normalize_types(v, failuresets, variables) for v in value]
    if is_dataclass(value):
        return replace(value, **{f.name: normalize_types(getattr(value, f.name), failuresets, variables)
                                 for f in fields(value)})
    return value


def build_symbols(prog: Program) -> Symbols:

    guarantees: Dict[str, GuaranteeDecl] = {}
    typegroups: Dict[str, set[TypeIdentityKey]] = {}
    type_guarantees: Dict[TypeIdentityKey, set[str]] = {}
    sigs: Dict[str, SigDecl] = {}
    sig_failures: Dict[str, FailureSet] = {}
    sig_conditional_failures: Dict[str, frozenset[ConditionalFailure]] = {}
    funcs: Dict[str, FuncDecl] = {}
    impls: Dict[Tuple[TypeIdentityKey, str, str], str] = {}

    items = prelude_items()
    items += list(prog.items)

    # Validate definitions before resolving references, for source, internal AST,
    # and catalog declarations alike. GuaranteeDecl currently supports methods
    # only; there is no standalone capability-requirement syntax or AST field.
    for item in items:
        if isinstance(item, GuaranteeDecl) and not item.methods:
            raise TypecheckError(
                f"guarantee {item.name} must declare at least one method or capability requirement"
            )

    # Resolve named sets before sig normalization, including catalog sigs.
    failuresets = _build_failuresets(items)
    typegroups = failuresets.typegroups

    items = normalize_types(items, failuresets)
    for item in items:

        if isinstance(item, GuaranteeDecl):
            if item.name in guarantees:
                raise TypecheckError(f"duplicate guarantee '{item.name}'")
            
            guarantees[item.name] = item
        
        if isinstance(item, TypeGroupDecl):
            pass  # Membership was collected before contract normalization.

        elif isinstance(item, SigDecl):

            if item.name in sigs:
                raise TypecheckError(f"duplicate sig '{item.name}'")
            
            sigs[item.name] = item

            attrs = set(getattr(item, "attrs", []) or [])

            # 未知の attr を禁止
            for a in attrs:
                if a not in ATTRS:
                    raise TypecheckError(f"unknown attr '@attr.{a}' on sig '{item.name}'")
                
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
                if attr not in ATTRS:
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

            parameter_names: set[str] = set()
            for param in item.params:
                if param.name in parameter_names:
                    raise TypecheckError(f"duplicate parameter name: '{param.name}'")
                parameter_names.add(param.name)
            
        elif isinstance(item, RegisterDecl):

            pass  # Resolve registration destinations after collecting declarations.

        elif isinstance(item, ImplDecl):
            _validate_concrete_identity(item.typ, "impl target")
            t = type_identity_key(item.typ)
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
            
        elif isinstance(item, VarDecl):
            pass  # checked later

    # Names share a namespace; membership and capability remain independent.
    for name in guarantees.keys() & typegroups.keys():
        raise TypecheckError(f"guarantee/typegroup name conflict '{name}'")

    for item in items:
        if not isinstance(item, RegisterDecl):
            continue
        _validate_concrete_identity(item.typ, "register target")
        target = item.guarantee
        if item.typ.name in typegroups:
            raise TypecheckError(f"typegroup '{item.typ.name}' is not a concrete register target")
        if target in typegroups:
            continue  # Already validated and registered before normalization.
        if target in guarantees:
            raise TypecheckError(
                f"register target must be a typegroup: {target} is a guarantee"
            )
        raise TypecheckError(f"unknown register target '{target}'; expected typegroup")

    syms = Symbols(
        guarantees=guarantees,
        typegroups=typegroups,
        type_guarantees=type_guarantees,
        sigs=sigs,
        sig_failures=sig_failures,
        sig_conditional_failures=sig_conditional_failures,
        failuresets=failuresets,
        funcs=funcs,
        impls=impls,
        binding_types={},
        expression_types={},
        resolved_calls={},
    )

    _validate_catalog(syms)
    return syms

# =====================
# Catalog validation
# =====================
def _validate_catalog(syms: Symbols) -> None:
    # Reference validity is independent of use, dispatch, and satisfaction.
    # All source and prelude declarations have been collected and normalized.
    for name, sig in syms.sigs.items():
        inferable_typevars = _collect_inferable_param_typevars(sig)
        for requirement in sig.requires:
            if requirement.type_var not in inferable_typevars:
                raise TypecheckError(
                    f"requirement type variable '{requirement.type_var}' is not inferable "
                    f"from parameters of function '{name}'"
                )
            if isinstance(requirement, RequireGuarantees):
                if requirement.guarantee_name not in syms.guarantees:
                    raise TypecheckError(
                        f"unknown guarantee '{requirement.guarantee_name}' "
                        f"in requirement for function '{name}'"
                    )
            elif isinstance(requirement, RequireIn):
                if requirement.group_name not in syms.typegroups:
                    raise TypecheckError(
                        f"unknown typegroup '{requirement.group_name}' "
                        f"in requirement for function '{name}'"
                    )

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
            if failure.group_name not in syms.typegroups:
                raise TypecheckError(f"unknown typegroup '{failure.group_name}' in failure condition of sig '{name}'")
            clause = ConditionalFailure(fid, failure.type_var, failure.group_name)
            if clause in clauses:
                raise TypecheckError(f"duplicate conditional failure in sig '{name}'")
            if fid in syms.sig_failures[name]:
                raise TypecheckError(f"redundant conditional failure '{fid.value}' in sig '{name}'")
            if "Never" in sig.failures:
                raise TypecheckError(f"cannot combine 'Never' with other failures in sig '{name}'")
            clauses.add(clause)
        syms.sig_conditional_failures[name] = frozenset(clauses)

    # 1) impl が参照する guarantee は存在するか
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

    # Mirror type_call's dispatch eligibility after all funcs are collected.
    # Only the concrete type is deferred; the guarantee and method name are fixed.
    for name, sig in syms.sigs.items():
        if name in syms.funcs or sig.builtin is not None:
            continue
        requirements = [r for r in sig.requires if isinstance(r, RequireGuarantees)]
        if len(requirements) != 1:
            continue
        guarantee = syms.guarantees.get(requirements[0].guarantee_name)
        if guarantee is None:
            continue
        methods = [method for method in guarantee.methods if method.name == name]
        if len(methods) != 1:
            continue
        method = methods[0]
        # self is an explicit parameter, passed along with every other argument.
        if len(sig.params) != len(method.params):
            raise TypecheckError(
                f"arity mismatch for guarantee method '{name}': "
                f"sig expects {len(sig.params)} argument(s), "
                f"guarantee {guarantee.name} method expects {len(method.params)}"
            )
        # Both declarations are normalized by build_symbols. The method is the
        # canonical type contract; projection requires identity, not widening.
        required_type_var = requirements[0].type_var
        for index, (actual, param) in enumerate(zip(sig.params, method.params), 1):
            expected = _project_method_type(param.typ, required_type_var)
            if not _same_type(expected, actual):
                raise TypecheckError(
                    f"parameter type mismatch for guarantee method '{name}' at argument {index}: "
                    f"sig has {actual}, guarantee {guarantee.name} requires {expected}"
                )
        expected = _project_method_type(method.ret, required_type_var)
        if not _same_type(expected, sig.ret):
            raise TypecheckError(
                f"return type mismatch for guarantee method '{name}': "
                f"sig has {sig.ret}, guarantee {guarantee.name} requires {expected}"
            )


def signature_contract(syms: Symbols, name: str) -> FailureContract:
    return FailureContract(syms.sig_failures[name], syms.sig_conditional_failures[name])


def instantiated_signature_failures(syms: Symbols, name: str, bindings) -> FailureSet:
    return substitute_contract(signature_contract(syms, name), bindings,
                               syms.typegroups).concrete_failures()
