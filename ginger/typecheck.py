#from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .ast import (
    Program,
    GuaranteeDecl,
    TypeGroupDecl,
    RegisterDecl,
    ImplDecl,
    FuncDecl,
    VarDecl,
    RequireIn,
    RequireGuarantees,
    TypeRef,
    Expr,
    CallExpr,
    IdentExpr,
    IntLit,
    FloatLit,
    PosArg,
    NamedArg,
    ExprStmt,
)


# =====================
# Errors
# =====================

@dataclass
class TypecheckError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message


@dataclass
class BindError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message


# =====================
# Symbols
# =====================

@dataclass(frozen=True)
class Symbols:
    guarantees: Dict[str, GuaranteeDecl]
    typegroups: Dict[str, Set[str]]                  # group -> {"Int","Float",...}
    type_guarantees: Dict[str, Set[str]]             # type -> {"Addable",...}
    funcs: Dict[str, FuncDecl]                       # name -> decl
    impls: Dict[Tuple[str, str, str], str]           # (Type, Guarantee, Method) -> builtin_id


def build_symbols(prog: Program) -> Symbols:
    guarantees: Dict[str, GuaranteeDecl] = {}
    typegroups: Dict[str, Set[str]] = {}
    type_guarantees: Dict[str, Set[str]] = {}
    funcs: Dict[str, FuncDecl] = {}
    impls: Dict[Tuple[str, str, str], str] = {}

    for item in prog.items:
        if isinstance(item, GuaranteeDecl):
            if item.name in guarantees:
                raise TypecheckError(f"duplicate guarantee '{item.name}'")
            guarantees[item.name] = item

        elif isinstance(item, TypeGroupDecl):
            if item.name in typegroups:
                raise TypecheckError(f"duplicate typegroup '{item.name}'")
            typegroups[item.name] = {t.name for t in item.members}

        elif isinstance(item, FuncDecl):
            if item.name in funcs:
                raise TypecheckError(f"duplicate func '{item.name}'")
            funcs[item.name] = item

        elif isinstance(item, RegisterDecl):
            t = item.typ.name
            g = item.guarantee
            if g in type_guarantees.get(t, set()):
                raise TypecheckError(f"duplicate register: '{t}' guarantees '{g}'")
            type_guarantees.setdefault(t, set()).add(g)

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

        elif isinstance(item, VarDecl):
            pass  # checked later

    syms = Symbols(
        guarantees=guarantees,
        typegroups=typegroups,
        type_guarantees=type_guarantees,
        funcs=funcs,
        impls=impls,
    )
    _validate_catalog(syms)
    return syms


# =====================
# Arg binding (positional vs named)
# =====================

def bind_args(call: CallExpr, func: FuncDecl) -> Dict[str, Expr]:
    """
    Returns dict[param_name -> Expr]
    """
    params = func.params
    param_names = [p.name for p in params]
    bound: Dict[str, Expr] = {}

    if call.arg_style == "pos":
        expected = len(param_names)
        got = len(call.args)
        if got != expected:
            raise BindError(
                f"argument count mismatch in call to {call.callee}: expected {expected}, got {got}"
            )
        for pname, arg in zip(param_names, call.args):
            assert isinstance(arg, PosArg)
            bound[pname] = arg.expr
        return bound

    if call.arg_style == "named":
        pset = set(param_names)

        for arg in call.args:
            assert isinstance(arg, NamedArg)

            if arg.name not in pset:
                raise BindError(
                    f"unknown named argument '{arg.name}' in call to {call.callee} "
                    f"(expected: {', '.join(param_names)})"
                )
            if arg.name in bound:
                raise BindError(f"duplicate named argument '{arg.name}' in call to {call.callee}")
            bound[arg.name] = arg.expr

        missing = [p for p in param_names if p not in bound]
        if missing:
            raise BindError(
                f"missing required argument(s) {', '.join(repr(m) for m in missing)} in call to {call.callee}"
            )
        return bound

    raise BindError(f"invalid arg_style '{call.arg_style}' in call to {call.callee}")


# =====================
# Type inference helpers
# =====================

def is_typevar(name: str) -> bool:
    # minimal rule: single uppercase letter is a type var (T, U, V...)
    return len(name) == 1 and name.isalpha() and name.isupper()


def resolve_typeref(t: TypeRef, tmap: Dict[str, str]) -> str:
    if is_typevar(t.name):
        if t.name not in tmap:
            raise TypecheckError(f"cannot resolve type variable '{t.name}'")
        return tmap[t.name]
    return t.name


# =====================
# Catalog validation
# =====================

def _validate_catalog(syms: Symbols) -> None:
    # register references known guarantees
    for t, gs in syms.type_guarantees.items():
        for g in gs:
            if g not in syms.guarantees:
                raise TypecheckError(f"type '{t}' references unknown guarantee '{g}'")

    # func require clauses reference known typegroups/guarantees
    for f in syms.funcs.values():
        for req in f.requires:
            if isinstance(req, RequireIn):
                if req.group_name not in syms.typegroups:
                    raise TypecheckError(f"func '{f.name}' requires unknown typegroup '{req.group_name}'")
            elif isinstance(req, RequireGuarantees):
                if req.guarantee_name not in syms.guarantees:
                    raise TypecheckError(f"func '{f.name}' requires unknown guarantee '{req.guarantee_name}'")

    # if a type is registered/impl'd as guaranteeing something, it must implement all methods
    for t, gs in syms.type_guarantees.items():
        for g in gs:
            gdecl = syms.guarantees[g]
            for msig in gdecl.methods:
                key = (t, g, msig.name)
                if key not in syms.impls:
                    # NOTE: If you want "register without impl" allowed, relax here.
                    raise TypecheckError(
                        f"type '{t}' guarantees '{g}' but missing impl for method '{msig.name}'"
                    )


# =====================
# Typechecking
# =====================

def typecheck_program(prog: Program) -> Dict[str, str]:
    """
    Typecheck all top-level VarDecls.
    Policy: assignment LHS decides generic type variables (e.g., T).
    Returns: env mapping var name -> type name.
    """
    syms = build_symbols(prog)
    env: Dict[str, str] = {}

    for item in prog.items:

        # Float x = add(1.3, 1.2)のような構文の場合
        if isinstance(item, VarDecl):
            expected = item.typ.name
            actual = type_expr(item.expr, expected, env, syms)

            if actual != expected:
                raise TypecheckError(
                    f"type mismatch in declaration '{item.name}: expected {expected}, got {actual}"
                )
            
            env[item.name] = expected
            continue

        if isinstance(item, ExprStmt):
            # print(x)のような構文の場合
            type_expr(item.expr, None, env, syms)
            continue

        # それ以外（Catalog/Impl/func etc）は型検査対象外
        continue

    return env

        
        #expected = item.typ.name
        #actual = type_expr(item.expr, expected, env, syms)

        #if actual != expected:
        #    raise TypecheckError(
        #        f"type mismatch in declaration '{item.name}': expected {expected}, got {actual}"
        #    )
        #env[item.name] = expected

    #return env


def type_expr(expr: Expr, expected: Optional[str], env: Dict[str, str], syms: Symbols) -> str:
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
        t = env[expr.name]
        if expected is not None and t != expected:
            raise TypecheckError(f"type mismatch: expected {expected}, got {t}")
        return t

    # call
    if isinstance(expr, CallExpr):
        return type_call(expr, expected, env, syms)

    raise TypecheckError(f"unsupported expr node: {expr!r}")


def type_call(call: CallExpr, expected: Optional[str], env: Dict[str, str], syms: Symbols) -> str:

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