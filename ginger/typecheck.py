from dataclasses import dataclass
from typing import Dict, Optional, Union
from .errors import TypecheckError
from .symbols_builder import build_symbols
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
    if a.name != b.name:
        return False
    if len(a.args) != len(b.args):
        return False
    return all(same_type(x, y) for x, y in zip(a.args, b.args))

def remove_failure(eff: FailureSet, name: str) -> FailureSet:
    return frozenset(f for f in eff if f.value != name)

def effect_expr(expr: Expr, env: Dict[str, Binding], syms) -> FailureSet:

    # literals
    if isinstance(expr, IntLit):
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
            raise TypecheckError(f"cannot resolve type variable '{t.name}'")
        return tmap[t.name]
    return t

# def resolve_typeref(t, tmap: Dict[str, TypeRef]) -> TypeRef:
#     if is_typevar(t.name):
#         if t.name not in tmap:
#             raise TypecheckError(f"cannot resolve type variable '{t.name}'")
#         return tmap[t.name]
#     return t


# =====================
# Typechecking
# =====================

def typecheck_program(prog, diags: Diagnostics) -> Dict[str, Binding]:

    syms = build_symbols(prog)
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

            #if t_try != "Unit":
            if not same_type(t_try, TypeRef("Unit")):
                raise TypecheckError(f"only Unit expression are allowed in try, got '{t_try}'")
            
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

                # その catch 自身の failure は中でも握る（ネスト禁止）
                e = remove_failure(e, c.failure_name)
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
            
            env[item.name] = Binding(ty=t, mutable=item.mutable)
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
    for item in prog.items:
        
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
            if isinstance(st, ReturnStmt):
                rt = type_expr(st.expr, expected=None, env=fenv, syms=syms, tv_guars=tv_guars)
                ret_types.append(rt)
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
        
        if not same_type(first, sig.ret):
            raise TypecheckError(
                f"func '{item.name}' return type mismatch: sig expects '{sig.ret.name}', got '{first}'"
            )

    # Keep all existing type checks (including unreachable statements) ahead of effects.
    typecheck_func_failures(syms, diags)


def reachable_body_exprs(func):
    """Only ReturnStmt/ExprStmt are supported by the preceding body typecheck."""
    for stmt in func.body.stmts:
        yield stmt.expr
        if isinstance(stmt, ReturnStmt):
            break


def has_thunk_type(typ: TypeRef) -> bool:
    return typ.name == "Thunk" or any(has_thunk_type(arg) for arg in typ.args)


def failure_contract_boundaries(fname, syms, visited=None) -> set[str]:
    """Do not certify handled/latent effects, including transitive dependencies.

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
    if fname in ("thunk", "force") or any(
        has_thunk_type(typ) for typ in [sig.ret, *sig.params]
    ):
        reasons.add(f"Thunk: {fname}")

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
        inferred_failures = union_failures(
            *(effect_expr(expr, env, syms) for expr in reachable_body_exprs(func))
        )
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

def type_expr(expr: Expr, expected: Optional[str], env: Dict[str, Binding], syms, tv_guars: Optional[Dict[str, set[str]]] = None) -> TypeRef:

    if tv_guars is None:
        tv_guars = {}

    # literals
    if isinstance(expr, IntLit):
    
        t = TypeRef("Int")

        if expected is not None and not same_type(t, expected):
            raise TypecheckError(f"type mismatch: expected {expected}, got {t}")

        return t

    if isinstance(expr, FloatLit):
        
        t = TypeRef("Float")

        if expected is not None and not same_type(t, expected):
            raise TypecheckError(f"type mismatch: expected {expected}, got {t}")

        return t

    # identifier
    if isinstance(expr, IdentExpr):
        
        if expr.name not in env:
            raise TypecheckError(f"unknown identifier '{expr.name}'")
        
        t = env[expr.name].ty

        if expected is not None and not same_type(t, expected):
            raise TypecheckError(f"type mismatch: expected {expected}, got {t}")
    
        return t

    # call
    if isinstance(expr, CallExpr):
        return type_call(expr, expected, env, syms, tv_guars=tv_guars)
    
def type_call(call: CallExpr,expected: Optional[TypeRef],env: Dict[str, Binding],syms,tv_guars: Optional[Dict[str, set[str]]] = None) -> TypeRef:

    if tv_guars is None:
        tv_guars = {}

    # =====================
    # special: thunk
    # =====================
    if call.callee == "thunk":
        if len(call.args) != 1:
            raise TypecheckError(
                f"argument count mismatch in call to thunk: expected 1, got {len(call.args)}"
            )
        arg_expr = call.args[0].expr

        inner_expected = None
        if isinstance(expected, TypeRef) and expected.name == "Thunk":
            inner_expected = expected.args[0]

        t = type_expr(arg_expr, inner_expected, env, syms, tv_guars)
        return TypeRef("Thunk", [t])

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
        if expected is not None and not same_type(result_type, expected):
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

    tmap: Dict[str, TypeRef] = {}

    # =====================
    # ① expected から型決定
    # =====================
    if expected is not None:
        if is_typevar(sig.ret.name):
            tmap[sig.ret.name] = expected
        else:
            if not same_type(TypeRef(sig.ret.name), to_typeref(expected)):
                raise TypecheckError(
                    f"type mismatch in call to {sig.name}: "
                    f"expected {expected}, got {sig.ret.name}"
                )
    else:
        if is_typevar(sig.ret.name):
            raise TypecheckError(
                f"cannot determine type variable '{sig.ret.name}' "
                f"in call to {sig.name} (no expected type)"
            )

    # =====================
    # 引数抽出
    # =====================
    arg_exprs = [a.expr for a in call.args]

    # =====================
    # ② 引数から型変数推論
    # =====================
    for tref, aexpr in zip(sig.params, arg_exprs):
        if is_typevar(tref.name) and tref.name not in tmap:
            inferred = type_expr(
                aexpr,
                tmap.get(tref.name),
                env,
                syms,
                tv_guars=tv_guars
            )
            tmap[tref.name] = inferred

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

    # =====================
    # ④ 引数型チェック
    # =====================
    for tref, aexpr in zip(sig.params, arg_exprs):
        expected_arg = resolve_typeref(tref, tmap)

        try:
            type_expr(aexpr, expected_arg, env, syms, tv_guars=tv_guars)
        except TypecheckError as e:
            if call.callee == "div":
                raise TypecheckError(
                    "division expects Float operands. "
                    "Write 1.0/2.0 or use toFloat(...)."
                ) from e
            raise

    # =====================
    # return型
    # =====================
    if is_typevar(sig.ret.name):
        return tmap[sig.ret.name]

    return TypeRef(sig.ret.name)
    

# def type_call(call: CallExpr, expected: Optional[str], env: Dict[str, Binding], syms, tv_guars: Optional[Dict[str, set[str]]] = None) -> TypeRef:

#     if call.callee == "thunk":
#         arg_expr = call.args[0].expr

#         inner_expected = None

#         # expected が Thunk[T] なら T を取り出す
#         if isinstance(expected, TypeRef) and expected.name == "Thunk":
#             inner_expected = expected.args[0]

#         t = type_expr(arg_expr, inner_expected, env, syms, tv_guars)

#         return TypeRef("Thunk", [t])

#     if tv_guars is None:
#         tv_guars = {}

#     if call.callee not in syms.sigs:
#         raise TypecheckError(f"call to undeclared sig '{call.callee}'")
    
#     sig = syms.sigs[call.callee]

#     # sig は引数名がないので、name args 禁止
#     if call.arg_style != "pos":
#         raise TypecheckError(f"named arguments are not allowed for calls to sig '{sig.name}'")
    
#     if len(call.args) != len(sig.params):
#         raise TypecheckError(
#             f"argument count mismatch in call to {sig.name}: expected {len(sig.params)}, got {len(call.args)}"
#         )

#     #bound = bind_args(call, sig)
#     tmap: Dict[str, TypeRef] = {}

#     # ① 代入先 or 特殊推論で決める
#     if call.callee == "force":
#         # force は引数から型が決まるのでスキップ
#         pass

#     elif expected is not None:
#         if is_typevar(sig.ret.name):
#             tmap[sig.ret.name] = expected
#         else:
#             if not same_type(TypeRef(sig.ret.name), to_typeref(expected)):
#                 raise TypecheckError(...)

#     else:
#         if is_typevar(sig.ret.name):
#             raise TypecheckError(
#                 f"cannot determine type variable '{sig.ret.name}' in call to {sig.name} (no expected type)"
#             )
        
#     # 引数exprを位置で取り出す
#     arg_exprs = [a.expr for a in call.args]

#     # ② 引数から型変数を推論
#     for tref, aexpr in zip(sig.params, arg_exprs):
#         if is_typevar(tref.name) and tref.name not in tmap:
#             inferred = type_expr(aexpr, tmap.get(tref.name), env, syms, tv_guars=tv_guars)
#             tmap[tref.name] = inferred
    
#     # ③ require チェック（既存）
#     for req in sig.requires:
#         if isinstance(req, RequireIn):
#             if req.type_var not in tmap:
#                 raise TypecheckError(
#                     f"cannot check requirement '{req.type_var} in {req.group_name}': "
#                     f"type variable '{req.type_var}' not determined in call to {sig.name}"
#                 )
#             concrete = tmap[req.type_var]
#             allowed = syms.typegroups.get(req.group_name, set())
#             if concrete.name not in allowed:
#                 raise TypecheckError(
#                     f"requirement not satisfied in call to {sig.name}: "
#                     f"{req.type_var} in {req.group_name} required, but {req.type_var} = {concrete}"
#                 )
            
#         elif isinstance(req, RequireGuarantees):
#             if req.type_var not in tmap:
#                 raise TypecheckError(...)

#             concrete = tmap[req.type_var]
#             has = syms.type_guarantees.get(concrete.name, set())

#             if req.guarantee_name not in has:
#                 raise TypecheckError(
#                     f"requirement not satisfied in call to {sig.name}: "
#                     f"{concrete} does not guarantee {req.guarantee_name}"
#                 )
            
#     # ④ 引数型チェック（enhanced error for div）
#     for tref, aexpr in zip(sig.params, arg_exprs):
#         expected_arg = resolve_typeref(tref, tmap)
#         try:
#             type_expr(aexpr, expected_arg, env, syms, tv_guars=tv_guars)
#         except TypecheckError as e:
#             # Make division errors actionable:
#             # div expects Float operands, so guide the user to write 1.0/2.0 or toFloat(...)
#             if call.callee == "div":
#                 raise TypecheckError(
#                     "division expects Float operands. "
#                     "Write 1.0/2.0 (Float literals) or convert with toFloat(...)."
#                 ) from e
#             raise

#     # return type
#     if is_typevar(sig.ret.name):
#         return tmap[sig.ret.name]
#     return TypeRef(sig.ret.name)
