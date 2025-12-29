from dataclasses import dataclass
from typing import Dict, Optional
from .args import bind_args
from .errors import TypecheckError
from .symbols_builder import build_symbols
from .core.failure_spec import FailureSet, EMPTY_FAILURES, failures, FailureId, union_failures

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
)

@dataclass(frozen=True)
class Binding:
    ty: str
    mutable: bool   # let=False, var=True

"""
@dataclass(frozen=True)
class Typed:
    ty: object = None
    eff: FailureSet = EMPTY_FAILURES

@dataclass(frozen=True)
class FuncSig:
    failures: FailureSet = EMPTY_FAILURES

def parse_failures(names: list[str]) -> FailureSet:
    return failures(*[FailureId(n) for n in names])

symtab_funcs: dict[str, FuncSig] = {}

def register_func(decl):
    # decl.failure_namesが["IOErr"]のように取れる想定
    sig = FuncSig(failures=parse_failures(decl.failure_names))
    symtab_funcs[decl.name] = sig

def typecheck_call(name: str, args_typed: list[Typed]) -> Typed:
    # call.name / call.args は ASTに合わせて読み替え
    args_typed = [effect_expr(a) for a in effect_call.args]
    callee_eff = func_failures.get(effect_call.name, EMPTY_FAILURES)
    sig = symtab_funcs[name]
    eff = union_failures(sig.failures, *[a.eff for a in args_typed])
    return Typed(ty=None, eff=eff)
"""

func_failures: dict[str, FailureSet] = {}

def _parse_failures(names: list[str]) -> FailureSet:
    # ["IOErr", "TimeErr"] -> frozenset({FailureId.IOErr, FailureId.TimeErr})
    return failures(*[FailureId(n) for n in names])

def register_func_decl(decl) -> None:
    names: list[str] = getattr(decl, "failure_names", [])
    func_failures[decl.name] = _parse_failures(names)

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
    
    raise TypecheckError(f"unsupported expr node for effect: {expr!r}")

def effect_call(call: CallExpr, env: Dict[str, Binding], syms) -> FailureSet:

    if call.callee == "print" and len(call.args) == 0:
        return EMPTY_FAILURES

    if call.callee not in syms.funcs:
        raise TypecheckError(f"unknown function '{call.callee}'")
    
    func = syms.funcs[call.callee]
    bound = bind_args(call, func)

    # 引数側のeffect（再帰）
    arg_effects: list[FailureSet] = []

    for p in func.params:
        arg_effects.append(effect_expr(bound[p.name], env, syms))

    # 関数宣言に付いているfailure
    callee_eff: FailureSet = syms.func_failures.get(call.callee, EMPTY_FAILURES)
    eff_args = union_failures(EMPTY_FAILURES, *arg_effects)

    # @noncritical なら callee の failure を落とす（引数の failure は残す）
    attrs = syms.func_attrs.get(call.callee, set())

    if "noncritical" in attrs:
        return eff_args

    return union_failures(callee_eff, eff_args)

# =====================
# Type inference helpers
# =====================

def is_typevar(name: str) -> bool:
    # minimal rule: single uppercase letter is a type var (T, U, V...)
    return len(name) == 1 and name.isalpha() and name.isupper()


def resolve_typeref(t, tmap: Dict[str, str]) -> str:
    if is_typevar(t.name):
        if t.name not in tmap:
            raise TypecheckError(f"cannot resolve type variable '{t.name}'")
        return tmap[t.name]
    return t.name


# =====================
# Typechecking
# =====================

def typecheck_program(prog) -> Dict[str, str]:
    
    syms = build_symbols(prog)
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

            if t_try != "Unit":
                raise TypecheckError(f"only Unit expression are allowed in try, got '{t_try}'")
            
            eff_try = effect_expr(item.expr, env=env, syms=syms)

            # try側から、catchされるfailureを全部消す
            caught = {c.failure_name for c in catches}

            for name in caught:
                eff_try = remove_failure(eff_try, name)

            # --- catch側 ---
            eff_handlers = EMPTY_FAILURES

            for c in catches:

                t_c = type_expr(c.expr, expected=None, env=env, syms=syms)

                if t_c != "Unit":
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
                print(f"warning: unhandled failures: {names}")
            
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

            t = type_expr(item.expr, expected=item.typ.name, env=env, syms=syms)
            eff = effect_expr(item.expr, env=env, syms=syms)

            if eff != EMPTY_FAILURES:
                names = ', '.join(sorted(f.value for f in eff))
                #raise TypecheckError(f"unhandled failures: {names}")
                print(f"warning: unhandled failures: {names}")
            
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
                print(f"warning: unhandled failures: {names}")
            
            i += 1
            continue

        # --- ExprStmt ---
        if isinstance(item, ExprStmt):
            
            t = type_expr(item.expr, expected=None, env=env, syms=syms)
            eff = effect_expr(item.expr, env=env, syms=syms)

            if eff != EMPTY_FAILURES:
                names = ', '.join(sorted(f.value for f in eff))
                print(f"warning: unhandled failures: {names}")
                #raise TypecheckError(f"unhandled failures: {names}")
            
            if t != "Unit":
                raise TypecheckError(f"only Unit expression are allowed as statements, got '{t}'")
            
            i += 1
            continue

        # --- それ以外（Catalog/Impl/func etc.）は型検査対象外 ---
        i += 1

    return env

def type_expr(expr: Expr, expected: Optional[str], env: Dict[str, Binding], syms) -> str:
    # literals
    if isinstance(expr, IntLit):
        if expected is not None and expected != "Int":
            raise TypecheckError(f"type mismatch: expected {expected}, got Int")
        return "Int"

    if isinstance(expr, FloatLit):
        if expected is not None and expected != "Float":
            raise TypecheckError(f"type mismatch: expected {expected}, got Float")
        return "Float"

    # identifier
    if isinstance(expr, IdentExpr):
        if expr.name not in env:
            raise TypecheckError(f"unknown identifier '{expr.name}'")
        t = env[expr.name].ty
        if expected is not None and t != expected:
            raise TypecheckError(f"type mismatch: expected {expected}, got {t}")
        return t

    # call
    if isinstance(expr, CallExpr):
        return type_call(expr, expected, env, syms)

    raise TypecheckError(f"unsupported expr node: {expr!r}")


def type_call(call: CallExpr, expected: Optional[str], env: Dict[str, Binding], syms) -> str:

    if call.callee == "print" and len(call.args) == 0:
        return "Unit"

    if call.callee not in syms.funcs:
        raise TypecheckError(f"unknown function '{call.callee}'")

    func = syms.funcs[call.callee]
    bound = bind_args(call, func)

    tmap: Dict[str, str] = {}

    # ① 代入先で決める（既存）
    if expected is not None:
        if is_typevar(func.ret.name):
            tmap[func.ret.name] = expected
        else:
            if func.ret.name != expected:
                raise TypecheckError(
                    f"type mismatch in call to {func.name}: expected {expected}, got {func.ret.name}"
                )
    else:
        if is_typevar(func.ret.name):
            # 代入先がなく、戻り値が型変数だと決められない（既存方針）
            raise TypecheckError(
                f"cannot determine type variable '{func.ret.name}' in call to {func.name} (no expected type)"
            )

    # ② ★追加：引数から型変数を推論（print(x) を通す）
    for p in func.params:
        if p.name not in bound:
            raise TypecheckError(f"internal error: param '{p.name}' not bound")

        if is_typevar(p.typ.name) and p.typ.name not in tmap:
            inferred = type_expr(bound[p.name], None, env, syms)  # expected=None で実型を得る
            tmap[p.typ.name] = inferred

    # ③ require チェック（既存）
    for req in func.requires:
        if isinstance(req, RequireIn):
            if req.type_var not in tmap:
                raise TypecheckError(
                    f"cannot check requirement '{req.type_var} in {req.group_name}': "
                    f"type variable '{req.type_var}' not determined in call to {func.name}"
                )
            concrete = tmap[req.type_var]
            allowed = syms.typegroups.get(req.group_name, set())
            if concrete not in allowed:
                raise TypecheckError(
                    f"requirement not satisfied in call to {func.name}: "
                    f"{req.type_var} in {req.group_name} required, but {req.type_var} = {concrete}"
                )

        elif isinstance(req, RequireGuarantees):
            if req.type_var not in tmap:
                raise TypecheckError(
                    f"cannot check requirement '{req.type_var} guarantees {req.guarantee_name}': "
                    f"type variable '{req.type_var}' not determined in call to {func.name}"
                )
            concrete = tmap[req.type_var]
            has = syms.type_guarantees.get(concrete, set())
            if req.guarantee_name not in has:
                raise TypecheckError(
                    f"requirement not satisfied in call to {func.name}: "
                    f"{concrete} does not guarantee {req.guarantee_name}"
                )

    # ④ 引数型チェック（既存）
    for p in func.params:
        expected_arg = resolve_typeref(p.typ, tmap)
        type_expr(bound[p.name], expected_arg, env, syms)

    # return type
    if is_typevar(func.ret.name):
        return tmap[func.ret.name]
    return func.ret.name