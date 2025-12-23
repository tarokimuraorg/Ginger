from typing import Dict, Union
from .args import bind_args
from .surface_func import SURFACE_FUNCS
from .base.funcs import BASE_FUNCS
from .runtime_dispatch import Dispatcher
from .symbols_builder import build_symbols
from .errors import EvalError
from .runtime_failures import RaisedFailure

from .ast import (
    FuncDecl,
    VarDecl,
    Expr,
    CallExpr,
    IdentExpr,
    IntLit,
    FloatLit,
    ExprStmt,
    TryStmt,
    CatchStmt,
)

# =====================
# Runtime
# =====================
Value = Union[int, float]

def eval_program(prog) -> Dict[str, Value]:
    
    syms = build_symbols(prog)
    env: Dict[str, Value] = {}

    i = 0

    while i < len(prog.items):

        item = prog.items[i]
        
        if isinstance(item, TryStmt):
            # catchを連鎖で収集
            j = i + 1
            catches = []

            while j < len(prog.items) and isinstance(prog.items[j], CatchStmt):
                catches.append(prog.items[j])
                j += 1

            if not catches:
                raise EvalError("try must be followed by at least one catch")
            
            try:
                # try本体（成功したら、catchは一切走らない）
                eval_expr(item.expr, env=env, syms=syms)
            except RaisedFailure as rf:

                handled = False

                for c in catches:

                    if rf.fid.value == c.failure_name:
                        handled = True

                        # ネスト禁止のため、catch内で同じ failure が起きたら握る
                        try:
                            eval_expr(c.expr, env=env, syms=syms)
                        except RaisedFailure as rf2:
                            if rf2.fid.value != c.failure_name:
                                raise
                        break
                
                if not handled:
                    raise   # 一致する catch が無ければ外へ
            
            i = j
            continue

        # catch単体は実行時もエラーにしておく
        if isinstance(item, CatchStmt):
            raise EvalError("catch without preceding try")
            
        if isinstance(item, VarDecl):
            v = eval_expr(item.expr, env=env, syms=syms)
            env[item.name] = v
            i += 1
            continue

        if isinstance(item, ExprStmt):
            eval_expr(item.expr, env=env, syms=syms)
            i += 1
            continue

        i += 1

    return env

def eval_expr(expr: Expr, env: Dict[str, Value], syms) -> Value:

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


def eval_call(call: CallExpr, env: Dict[str, Value], syms):

    if call.callee not in syms.funcs:
        raise EvalError(f"unknown function '{call.callee}'")
    
    func: FuncDecl = syms.funcs[call.callee]
    bound = bind_args(call, func)

    args = [eval_expr(bound[p.name], env, syms) for p in func.params]

    dispatch = Dispatcher(syms)

    BUILTINS = {}
    BUILTINS.update(SURFACE_FUNCS)
    BUILTINS.update(BASE_FUNCS)
    # BUILTINS.update(CORE_FUNCS)

    if func.name in BUILTINS:
        return BUILTINS[func.name](args, dispatch)
    
    raise EvalError(f"function '{func.name}' has no runtime implementation yet")