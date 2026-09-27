from dataclasses import dataclass
from typing import Dict, Optional, Union
from .errors import TypecheckError
from .numeric import can_widen, INT_MIN, INT_MAX, INT64_MIN, INT64_MAX
from .builtin import INT_ARITHMETIC_FAILURES
from .symbols_builder import build_symbols, normalize_types, ResolvedCall
from ginger.core.failure_spec import failures, FailureId, FailureSet, EMPTY_FAILURES, union_failures
from .diagnostics import Diagnostics

from .ast import (
    VarDecl,
    AssignStmt,
    RequireIn,
    RequireGuarantees,
    Expr,
    CallExpr,
    IdentExpr,
    IntLit,
    Int64Lit,
    FloatLit,
    ExprStmt,
    TryStmt,
    CatchStmt,
    FuncDecl,
    BlockStmt,
    ReturnStmt,
    TypeRef,
)

@dataclass(frozen=True)
class Binding:
    ty: TypeRef
    mutable: bool   # let=False, var=True

def same_type(a: TypeRef, b: TypeRef) -> bool:
    if a.latent_failures != b.latent_failures:
        return False
    if a.name != b.name:
        return False
    if len(a.args) != len(b.args):
        return False
    return all(same_type(x, y) for x, y in zip(a.args, b.args))

def compatible(actual, declared):
    if actual.name == declared.name == "Thunk":
        return (same_type(actual.args[0], declared.args[0])
                and actual.latent_failures <= declared.latent_failures)
    return same_type(actual, declared) or can_widen(actual, declared)


def remove_failure(eff: FailureSet, name: str) -> FailureSet:
    return frozenset(f for f in eff if f.value != name)

def effect_expr(expr: Expr, env: Dict[str, Binding], syms) -> FailureSet:

    # literals
    if isinstance(expr, (IntLit, Int64Lit)):
        return EMPTY_FAILURES
    
    if isinstance(expr, FloatLit):
        return EMPTY_FAILURES
    
    # identifier
    if isinstance(expr, IdentExpr):
        return EMPTY_FAILURES
    
    # call
    if isinstance(expr, CallExpr):
        return effect_call(expr, env, syms)
    
    
def effect_call(call: CallExpr, env: Dict[str, Binding], syms) -> FailureSet:

    if call.callee not in syms.sigs:
        raise TypecheckError(f"call to undeclared function '{call.callee}'")
    
    sig = syms.sigs[call.callee]
    if call.callee == "thunk":
        return EMPTY_FAILURES
    if call.callee == "force":
        arg = call.args[0].expr
        # Use the type checked with its original context, including generic calls.
        typ = syms.expression_types.get(id(arg))
        if typ is None:
            typ = type_expr(arg, None, env, syms)
        return union_failures(effect_expr(arg, env, syms), typ.latent_failures)

    # sig は引数名がないので named args 禁止
    if call.arg_style != "pos":
        raise TypecheckError(f"named arguments are not allowed for calls to sig '{sig.name}'")
    
    if len(call.args) != len(sig.params):
        raise TypecheckError(
            f"argument count mismatch in call to {sig.name}: expected {len(sig.params)}, got {len(call.args)}"
            )
    
    # 引数側のeffect
    arg_effects: list[FailureSet] = []

    for a in call.args:
        # pos only
        arg_effects.append(effect_expr(a.expr, env, syms))

    callee_eff: FailureSet = syms.sig_failures.get(call.callee, EMPTY_FAILURES)
    # The checked call's implementation is the same one runtime dispatch uses.
    if call.callee not in syms.funcs and sig.builtin is None:
        if id(call) not in syms.resolved_calls:
            type_expr(call, None, env, syms)
        resolved = syms.resolved_calls[id(call)]
        callee_eff = union_failures(
            callee_eff, INT_ARITHMETIC_FAILURES.get(resolved.implementation, EMPTY_FAILURES)
        )
    eff_args = union_failures(EMPTY_FAILURES, *arg_effects)

    # @handled なら callee の failure を落とす（引数の failure は残す）
    attrs = syms.sig_attrs.get(call.callee, set())

    if "handled" in attrs:
        return eff_args

    return union_failures(callee_eff, eff_args)

# =====================
# Type inference helpers
# =====================

def is_typevar(name: str) -> bool:
    # minimal rule: single uppercase letter is a type var (T, U, V...)
    return len(name) == 1 and name.isalpha() and name.isupper()

def resolve_typeref(t: TypeRef, tmap: Dict[str, TypeRef]) -> TypeRef:
    if is_typevar(t.name):
        if t.name not in tmap:
            raise TypecheckError(f"cannot determine type variable '{t.name}'")
        return tmap[t.name]
    return TypeRef(t.name, tuple(resolve_typeref(a, tmap) for a in t.args),
                   latent_failures=t.latent_failures)

# def resolve_typeref(t, tmap: Dict[str, TypeRef]) -> TypeRef:
#     if is_typevar(t.name):
#         if t.name not in tmap:
#             raise TypecheckError(f"cannot determine type variable '{t.name}'")
#         return tmap[t.name]
#     return t


# =====================
# Typechecking
# =====================

def typecheck_program(prog, diags: Diagnostics, *, syms=None) -> Dict[str, Binding]:

    if syms is None:
        syms = build_symbols(prog)
        prog = normalize_types(prog, syms.failuresets)
    typecheck_func_bodies(prog, syms, diags)
    env: Dict[str, Binding] = {}

    i = 0

    while i < len(prog.items):

        item = prog.items[i]

        # --- try/catch (2行セット) ---
        if isinstance(item, TryStmt):
            # catch連鎖を集める
            j = i + 1
            catches = []

            while j < len(prog.items) and isinstance(prog.items[j], CatchStmt):
                catches.append(prog.items[j])
                j += 1

            if not catches:
                raise TypecheckError("try must be followed by at least one catch")
            
            # --- try側 ---
            t_try = type_expr(item.expr, expected=None, env=env, syms=syms)

            original_try_effects = effect_expr(item.expr, env=env, syms=syms)
            for c in catches:
                try:
                    fid = FailureId(c.failure_name)
                except ValueError:
                    raise TypecheckError(f"unknown failure '{c.failure_name}' in catch") from None
                if fid not in original_try_effects:
                    raise TypecheckError(
                        f"cannot catch '{c.failure_name}': try expression has no declared or inferred "
                        f"{c.failure_name} failure"
                    )
            eff_try = original_try_effects

            # try側から、catchされるfailureを全部消す
            caught = {c.failure_name for c in catches}

            for name in caught:
                eff_try = remove_failure(eff_try, name)

            # --- catch側 ---
            eff_handlers = EMPTY_FAILURES

            for c in catches:

                t_c = type_expr(c.expr, expected=None, env=env, syms=syms)

                #if t_c != "Unit":
                if not same_type(t_c, TypeRef("Unit")):
                    raise TypecheckError(
                        f"only Unit expression are allowed in catch, got '{t_c}'"
                    )
                
                e = effect_expr(c.expr, env=env, syms=syms)

                # Catch clauses handle only try effects, never handler effects.
                eff_handlers = union_failures(eff_handlers, e)

            eff = union_failures(eff_try, eff_handlers)

            if eff != EMPTY_FAILURES:
                names = ", ".join(f.value for f in eff)
                diags.warn("UNHANDLED_FAILURES", f"unhandled failures: {names}")
            
            # TryStmt + 連鎖 CatchStmt を全部消費
            i = j
            continue
            
        # --- catch 単体は禁止 (try が消費するのは「次行の catch」のみ) ---
        if isinstance(item, CatchStmt):
            raise TypecheckError("catch without preceding try.")

        # --- VarDecl ---
        if isinstance(item, VarDecl):

            if item.name in env:
                raise TypecheckError(f"variable '{item.name}' already defined")

            t = type_expr(item.expr, expected=item.typ, env=env, syms=syms)
            eff = effect_expr(item.expr, env=env, syms=syms)

            if eff != EMPTY_FAILURES:
                names = ', '.join(sorted(f.value for f in eff))
                diags.warn("UNHANDLED_FAILURES", f"unhandled failures: {names}")
            
            env[item.name] = Binding(ty=item.typ, mutable=item.mutable)
            i += 1
            continue

        # --- AssignStmt ---
        if isinstance(item, AssignStmt):
            
            if item.name not in env:
                raise TypecheckError(f"unknown identifier '{item.name}'")
            
            b = env[item.name]

            if not b.mutable:
                raise TypecheckError(f"cannot assign to immutable binding '{item.name}'")
            
            # 代入先の型に合わせて右辺をチェック
            t = type_expr(item.expr, expected=b.ty, env=env, syms=syms)
            eff = effect_expr(item.expr, env=env, syms=syms)

            if eff != EMPTY_FAILURES:
                names = ", ".join(sorted(f.value for f in eff))
                diags.warn("UNHANDLED_FAILURES", f"unhandled failures: {names}")
            
            i += 1
            continue

        # --- ExprStmt ---
        if isinstance(item, ExprStmt):
            
            t = type_expr(item.expr, expected=None, env=env, syms=syms)
            eff = effect_expr(item.expr, env=env, syms=syms)

            if eff != EMPTY_FAILURES:
                names = ', '.join(sorted(f.value for f in eff))
                diags.warn("UNHANDLED_FAILURES", f"unhandled failures: {names}")

            if not same_type(t, TypeRef("Unit")):
            # if t != "Unit":
                 raise TypecheckError(f"only Unit expression are allowed as statements, got '{t}'")
            
            i += 1
            continue

        # --- それ以外（Catalog/Impl/func etc.）は型検査対象外 ---
        i += 1

    return env

def typecheck_func_bodies(prog, syms, diags: Optional[Diagnostics] = None) -> None:

    # func の本文を sig に照合する（return型のみ確認）
    for item in syms.funcs.values():
        
        if not isinstance(item, FuncDecl):
            continue
        
        if item.name not in syms.sigs:
            # build_symbols で弾かれている想定だが保険として
            raise TypecheckError(f"func '{item.name}' has no corresponding sig '{item.name}'")
        
        sig = syms.sigs[item.name]

        # sig.requires から「型変数が保証する guarantee」を収集
        tv_guars: Dict[str, set[str]] = {}

        for req in sig.requires:
            if isinstance(req, RequireGuarantees):
                tv_guars.setdefault(req.type_var, set()).add(req.guarantee_name)

        # 関数ローカル環境（引数束縛）
        fenv: Dict[str, Binding] = {}

        for p in item.params:
            fenv[p.name] = Binding(ty=p.typ, mutable=False)

        # ブロックを走査して return 型を集める
        ret_types: list[TypeRef] = []

        for st in item.body.stmts:
            if isinstance(st, VarDecl):
                if st.name in fenv:
                    raise TypecheckError(f"variable '{st.name}' already defined")
                type_expr(st.expr, expected=st.typ, env=fenv, syms=syms, tv_guars=tv_guars)
                # A declaration enters scope only after its initializer is checked.
                fenv[st.name] = Binding(ty=st.typ, mutable=st.mutable)
            elif isinstance(st, AssignStmt):
                if st.name not in fenv:
                    raise TypecheckError(f"unknown identifier '{st.name}'")
                binding = fenv[st.name]
                if not binding.mutable:
                    raise TypecheckError(f"cannot assign to immutable binding '{st.name}'")
                type_expr(st.expr, expected=binding.ty, env=fenv, syms=syms, tv_guars=tv_guars)
            elif isinstance(st, ReturnStmt):
                rt = type_expr(st.expr, expected=None, env=fenv, syms=syms, tv_guars=tv_guars)
                # Compare returns after boundary widening while retaining each
                # expression's actual type annotation for runtime coercion.
                ret_types.append(sig.ret if can_widen(rt, sig.ret) else rt)
            elif isinstance(st, ExprStmt):
                type_expr(st.expr, expected=None, env=fenv, syms=syms, tv_guars=tv_guars)
            else:
                # 他のstmtはまだfunc内で未対応
                raise TypecheckError(f"unsupported statement in func '{item.name}': {st!r}")
        
        # returnがない場合は Unit に相当するものを返す
        if not ret_types:
            if sig.ret.name != "Unit":
                raise TypecheckError(f"func '{item.name}' must return '{sig.ret.name}', but has no return (implicit Unit)")
            # sig が Unit なら OK
            continue
        
        # return がある場合：型が揃っていることを確認（今は return は1種類であることを要求）
        first = ret_types[0]

        if any(not same_type(t, first) for t in ret_types):
            raise TypecheckError(f"func '{item.name}' has inconsistent return types: {ret_types}")
        
        if not compatible(first, sig.ret):
            raise TypecheckError(
                f"func '{item.name}' return type mismatch: sig expects '{sig.ret.name}', got '{first}'"
            )

    # Keep all existing type checks (including unreachable statements) ahead of effects.
    typecheck_func_failures(syms, diags)


def reachable_body_exprs(func):
    """Include declaration/assignment RHS effects, stopping at the first return."""
    for stmt in func.body.stmts:
        yield stmt.expr
        if isinstance(stmt, ReturnStmt):
            break


def failure_contract_boundaries(fname, syms, visited=None) -> set[str]:
    """Do not certify handled effects, including transitive dependencies.

    Other builtin declarations remain trusted contracts, not inferred Python effects.
    This traversal records validation limits only; it does not change call effects.
    """
    if visited is None:
        visited = set()
    if fname in visited:
        return set()
    visited.add(fname)
    sig = syms.sigs[fname]
    func = syms.funcs.get(fname)
    reasons = set()
    if "handled" in sig.attrs or (func is not None and "handled" in func.attrs):
        reasons.add(f"handled: {fname}")

    def visit_expr(expr):
        if isinstance(expr, CallExpr):
            reasons.update(failure_contract_boundaries(expr.callee, syms, visited))
            for arg in expr.args:
                visit_expr(arg.expr)

    if func is not None:
        for expr in reachable_body_exprs(func):
            visit_expr(expr)
    return reasons


def typecheck_func_failures(syms, diags: Optional[Diagnostics] = None) -> None:
    for fname, func in syms.funcs.items():
        boundaries = failure_contract_boundaries(fname, syms)
        if boundaries:
            if diags is not None:
                diags.note(
                    "FAILURE_CONTRACT_DEFERRED",
                    f"failure upper-bound validation deferred for func '{fname}': "
                    + ", ".join(sorted(boundaries)),
                )
            continue
        env = {p.name: Binding(ty=p.typ, mutable=False) for p in func.params}
        inferred_failures = EMPTY_FAILURES
        for stmt in func.body.stmts:
            inferred_failures = union_failures(
                inferred_failures, effect_expr(stmt.expr, env, syms)
            )
            if isinstance(stmt, VarDecl):
                env[stmt.name] = Binding(ty=stmt.typ, mutable=stmt.mutable)
            if isinstance(stmt, ReturnStmt):
                break
        declared_failures = syms.sig_failures[fname]
        missing = inferred_failures - declared_failures
        if missing:
            names = ", ".join(sorted(f.value for f in missing))
            declared = ", ".join(sorted(f.value for f in declared_failures)) or "Never"
            raise TypecheckError(
                f"func '{fname}' may propagate undeclared failures: {names}; "
                f"declared failures: {declared}"
            )

def to_typeref(x):
    return x if isinstance(x, TypeRef) else TypeRef(x)

def type_expr(expr, expected, env, syms, tv_guars=None):
    typ = _type_expr(expr, expected, env, syms, tv_guars)
    if expected is not None and not compatible(typ, expected):
        raise TypecheckError(f"type mismatch: expected {expected}, got {typ}")
    syms.expression_types[id(expr)] = typ
    return typ

def _type_expr(expr: Expr, expected: Optional[TypeRef], env: Dict[str, Binding], syms, tv_guars: Optional[Dict[str, set[str]]] = None) -> TypeRef:

    if tv_guars is None:
        tv_guars = {}

    # literals
    if isinstance(expr, (IntLit, Int64Lit)):
    
        is64 = isinstance(expr, Int64Lit)
        t = TypeRef("Int64" if is64 else "Int")
        low, high = (INT64_MIN, INT64_MAX) if is64 else (INT_MIN, INT_MAX)
        if not low <= expr.value <= high:
            raise TypecheckError(f"{t.name} literal out of range: {expr.value} (expected {low}..{high})")

        if expected is not None and not compatible(t, expected):
            raise TypecheckError(f"type mismatch: expected {expected}, got {t}")

        return t

    if isinstance(expr, FloatLit):
        
        t = TypeRef("Float")

        if expected is not None and not compatible(t, expected):
            raise TypecheckError(f"type mismatch: expected {expected}, got {t}")

        return t

    # identifier
    if isinstance(expr, IdentExpr):
        
        if expr.name not in env:
            raise TypecheckError(f"unknown identifier '{expr.name}'")
        
        t = env[expr.name].ty

        if expected is not None and not compatible(t, expected):
            raise TypecheckError(f"type mismatch: expected {expected}, got {t}")
    
        return t

    # call
    if isinstance(expr, CallExpr):
        return type_call(expr, expected, env, syms, tv_guars=tv_guars)
    
def collect_type_evidence(formal: TypeRef, actual: TypeRef, evidence):
    """Match constructors recursively; failure sets remain concrete contracts."""
    if is_typevar(formal.name):
        evidence.setdefault(formal.name, []).append(actual)
        return
    if formal.name != actual.name or len(formal.args) != len(actual.args):
        if can_widen(actual, formal):
            return
        raise TypecheckError(f"type mismatch: expected {formal}, got {actual}")
    for formal_arg, actual_arg in zip(formal.args, actual.args):
        collect_type_evidence(formal_arg, actual_arg, evidence)


def reconcile_type_evidence(name: str, evidence: list[TypeRef]) -> TypeRef:
    """Choose a unique observed type accepting all evidence via safe widening."""
    observed = []
    for typ in evidence:
        if not any(same_type(typ, prior) for prior in observed):
            observed.append(typ)
    candidates = [candidate for candidate in observed
                  if all(same_type(actual, candidate) or can_widen(actual, candidate)
                         for actual in observed)]
    if not candidates:
        names = " and ".join(sorted(t.name for t in observed))
        raise TypecheckError(f"cannot reconcile {names} for type variable '{name}'")
    if len(candidates) != 1:
        raise TypecheckError(f"ambiguous evidence for type variable '{name}': {candidates}")
    return candidates[0]


def type_call(call: CallExpr,expected: Optional[TypeRef],env: Dict[str, Binding],syms,tv_guars: Optional[Dict[str, set[str]]] = None) -> TypeRef:

    if tv_guars is None:
        tv_guars = {}

    if call.callee in ("thunk", "force") and call.arg_style != "pos":
        raise TypecheckError("named arguments are not allowed for thunk/force")

    # =====================
    # special: thunk
    # =====================
    if call.callee == "thunk":
        if len(call.args) != 1:
            raise TypecheckError(
                f"argument count mismatch in call to thunk: expected 1, got {len(call.args)}"
            )
        arg_expr = call.args[0].expr

        t = type_expr(arg_expr, None, env, syms, tv_guars)
        return TypeRef("Thunk", (t,), latent_failures=effect_expr(arg_expr, env, syms))

    # =====================
    # special: force
    # =====================
    if call.callee == "force":
        if len(call.args) != 1:
            raise TypecheckError(
                f"argument count mismatch in call to force: expected 1, got {len(call.args)}"
            )
        arg_expr = call.args[0].expr
        t = type_expr(arg_expr, None, env, syms, tv_guars)

        if t.name != "Thunk":
            raise TypecheckError("force expects Thunk")

        result_type = t.args[0]
        if expected is not None and not compatible(result_type, expected):
            raise TypecheckError(f"type mismatch: expected {expected}, got {result_type}")

        return result_type

    # =====================
    # normal sig call
    # =====================
    if call.callee not in syms.sigs:
        raise TypecheckError(f"call to undeclared sig '{call.callee}'")

    sig = syms.sigs[call.callee]

    # 引数スタイルチェック
    if call.arg_style != "pos":
        raise TypecheckError(
            f"named arguments are not allowed for calls to sig '{sig.name}'"
        )

    if len(call.args) != len(sig.params):
        raise TypecheckError(
            f"argument count mismatch in call to {sig.name}: "
            f"expected {len(sig.params)}, got {len(call.args)}"
        )

    # Infer every argument independently; outer expected types are not evidence.
    actual_types = [type_expr(a.expr, None, env, syms, tv_guars) for a in call.args]
    evidence: Dict[str, list[TypeRef]] = {}
    for formal, actual in zip(sig.params, actual_types):
        try:
            collect_type_evidence(formal, actual, evidence)
        except TypecheckError as error:
            if call.callee == "div":
                raise TypecheckError("division expects Float operands. Write 1.0/2.0 or use toFloat(...).") from error
            raise
    tmap = {name: reconcile_type_evidence(name, candidates)
            for name, candidates in evidence.items()}
    parameter_types = tuple(resolve_typeref(t, tmap) for t in sig.params)
    return_type = resolve_typeref(sig.ret, tmap)

    # =====================
    # ③ requireチェック
    # =====================
    for req in sig.requires:

        if isinstance(req, RequireIn):
            if req.type_var not in tmap:
                raise TypecheckError(
                    f"cannot check requirement '{req.type_var} in {req.group_name}': "
                    f"type variable '{req.type_var}' not determined in call to {sig.name}"
                )

            concrete = tmap[req.type_var]
            allowed = syms.typegroups.get(req.group_name, set())

            if concrete.name not in allowed:
                raise TypecheckError(
                    f"requirement not satisfied in call to {sig.name}: "
                    f"{req.type_var} in {req.group_name} required, "
                    f"but {req.type_var} = {concrete}"
                )

        elif isinstance(req, RequireGuarantees):
            if req.type_var not in tmap:
                raise TypecheckError(
                    f"cannot check requirement '{req.type_var} guarantees {req.guarantee_name}': "
                    f"type variable '{req.type_var}' not determined in call to {sig.name}"
                )

            concrete = tmap[req.type_var]
            has = syms.type_guarantees.get(concrete.name, set())

            if req.guarantee_name not in has:
                raise TypecheckError(
                    f"requirement not satisfied in call to {sig.name}: "
                    f"{concrete} does not guarantee {req.guarantee_name}"
                )

    # Validate substituted contracts, including invariant constructor arguments
    # and the existing Thunk latent-failure upper bound.
    for actual, parameter in zip(actual_types, parameter_types):
        if not compatible(actual, parameter):
            if call.callee == "div":
                raise TypecheckError("division expects Float operands. Write 1.0/2.0 or use toFloat(...).")
            raise TypecheckError(f"type mismatch: expected {parameter}, got {actual}")

    implementation = None
    if call.callee not in syms.funcs:
        implementation = sig.builtin
        if implementation is None:
            requirements = [r for r in sig.requires if isinstance(r, RequireGuarantees)]
            if len(requirements) == 1:
                requirement = requirements[0]
                selected_type = tmap[requirement.type_var]
                implementation = syms.impls.get((selected_type.name, requirement.guarantee_name, sig.name))
    syms.resolved_calls[id(call)] = ResolvedCall(tmap, parameter_types, return_type, implementation)
    return return_type
