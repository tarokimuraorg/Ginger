#from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Union

from .ast import (
    Program,
    FuncDecl,
    VarDecl,
    Expr,
    CallExpr,
    IdentExpr,
    IntLit,
    FloatLit,
    ExprStmt,
)

from .typecheck import build_symbols, bind_args, Symbols
from .builtin import call_builtin, has_builtin

# =====================
# Runtime
# =====================

@dataclass
class EvalError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message


Value = Union[int, float]


# Ginger-stable builtin IDs (NOT Python names)
"""
BUILTINS = {
    "core.int.add":   lambda a, b: a + b,
    "core.float.add": lambda a, b: a + b,
    "core.print":     lambda x: (print(x), None)[1],
}
"""

def eval_program(prog: Program) -> Dict[str, Value]:
    """
    Evaluate all top-level VarDecls in order.
    Assumes typecheck already passed (recommended).
    """
    syms = build_symbols(prog)
    env: Dict[str, Value] = {}

    for item in prog.items:
        if isinstance(item, VarDecl):
            env[item.name] = eval_expr(item.expr, env, syms)
        elif isinstance(item, ExprStmt):
            eval_expr(item.expr, env, syms)
        else:
            # Catalog / Impl の宣言などはスルー
            continue

    return env


def eval_expr(expr: Expr, env: Dict[str, Value], syms: Symbols) -> Value:
    if isinstance(expr, IntLit):
        return int(expr.value)

    if isinstance(expr, FloatLit):
        return float(expr.value)

    if isinstance(expr, IdentExpr):
        if expr.name not in env:
            raise EvalError(f"unknown identifier '{expr.name}'")
        return env[expr.name]

    if isinstance(expr, CallExpr):
        return eval_call(expr, env, syms)

    raise EvalError(f"unsupported expr node: {expr!r}")


def _runtime_type_to_ginger(v: Value) -> str:
    # minimal runtime type mapping
    if isinstance(v, bool):
        # bool is subclass of int in Python; explicitly reject
        raise EvalError("unsupported runtime type: bool")
    if isinstance(v, int):
        return "Int"
    if isinstance(v, float):
        return "Float"
    raise EvalError(f"unsupported runtime type: {type(v).__name__}")

def type_of(v):
    if isinstance(v, int): return "Int"
    if isinstance(v, float): return "Float"
    if isinstance(v, str): return "String"
    if v is None: return "Unit"
    raise EvalError(f"unknown runtime value type: {type(v)}")

def call_impl_method(syms, typ: str, guarantee: str, method: str, receiver):
    key = (typ, guarantee, method)
    if key not in syms.impls:
        raise EvalError(f"missing impl: {typ} guarantees {guarantee}.{method}")
    builtin_name = syms.impls[key]
    return call_builtin(builtin_name, receiver)

def eval_call(call: CallExpr, env: Dict[str, Value], syms: Symbols) -> Value:
    if call.callee not in syms.funcs:
        raise EvalError(f"unknown function '{call.callee}'")

    func: FuncDecl = syms.funcs[call.callee]
    bound = bind_args(call, func)

    # 引数を評価（順序は関数定義順）
    args = [eval_expr(bound[p.name], env, syms) for p in func.params]

    # ---- print (builtin function) ----
    if func.name == "print":
        if len(args) != 1:
            raise EvalError("print expects exactly one argument")
        return call_impl_method(syms=syms, typ=type_of(args[0]), guarantee="Printable", method="print", receiver=args[0])
        #return call_builtin("core.print", args[0])
        #return BUILTINS["core.print"](args[0]).

    # ---- add (Addable guarantee dispatch) ----
    if func.name == "add":
        if len(args) != 2:
            raise EvalError("internal error: add expects 2 args")

        a, b = args
        if type(a) is not type(b):
            raise EvalError("add requires both arguments to have the same runtime type")

        tname = _runtime_type_to_ginger(a)
        key = (tname, "Addable", "add")

        if key not in syms.impls:
            raise EvalError(
                f"missing impl for type '{tname}' guarantee 'Addable' method 'add'"
            )

        builtin_id = syms.impls[key]

        if not has_builtin(builtin_id):
            raise EvalError(f"unknown builtin '{builtin_id}'")
        
        return call_builtin(builtin_id, a, b)

        """
        if builtin_id not in BUILTINS:
            raise EvalError(f"unknown builtin '{builtin_id}'")

        return BUILTINS[builtin_id](a, b)
        """
        

    raise EvalError(
        f"function '{func.name}' has no runtime implementation yet"
    )