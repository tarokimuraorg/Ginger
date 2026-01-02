from typing import Dict, Union, Optional
from dataclasses import dataclass
from .args import bind_args
from ginger.surface.funcs import SURFACE_FUNCS
from .base.funcs import BASE_FUNCS
from ginger.runtime.dispatch import Dispatcher
from .symbols_builder import build_symbols
from .errors import EvalError
from ginger.runtime.failures import RaisedFailure

from .ast import (
    SigDecl,
    FuncDecl,
    VarDecl,
    AssignStmt,
    Expr,
    CallExpr,
    IdentExpr,
    IntLit,
    FloatLit,
    BinaryExpr,
    BlockStmt,
    ReturnStmt,
    ExprStmt,
    TryStmt,
    CatchStmt,
    BinaryExpr,
)


# =====================
# Runtime
# =====================
Value = Union[int, float]

@dataclass
class Cell:
    value: Value
    #value: Union[int, float]
    mutable: bool   # let=False, var=True

@dataclass
class ReturnSignal(Exception):
    value: "Value"

def eval_program(prog) -> Dict[str, Cell]:
    
    syms = build_symbols(prog)
    env: Dict[str, Cell] = {}

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
            env[item.name] = Cell(value=v, mutable=item.mutable)
            i += 1
            continue

        if isinstance(item, AssignStmt):
            if item.name not in env:
                raise EvalError(f"unknown identifier '{item.name}'")
            cell = env[item.name]
            if not cell.mutable:
                raise EvalError(f"cannot assign to immutable binding '{item.name}'")
            v = eval_expr(item.expr, env=env, syms=syms)
            env[item.name] = Cell(value=v, mutable=cell.mutable)
            i += 1
            continue

        if isinstance(item, ExprStmt):
            eval_expr(item.expr, env=env, syms=syms)
            i += 1
            continue

        i += 1

    return env

def eval_expr(expr: Expr, env: Dict[str, Cell], syms) -> Value:

    if isinstance(expr, IntLit):
        return int(expr.value)

    if isinstance(expr, FloatLit):
        return float(expr.value)

    if isinstance(expr, IdentExpr):
        if expr.name in env:
            return env[expr.name].value
        raise EvalError(f"unknown identifier '{expr.name}'")

    if isinstance(expr, BinaryExpr):
        
        if expr.op != "+":
            raise EvalError(f"unsupported operator '{expr.op}'")
        
        lv = eval_expr(expr.left, env=env, syms=syms)
        rv = eval_expr(expr.right, env=env, syms=syms)

        dispach = Dispatcher(syms)
        typ = dispach.type_of(lv)

        # Addable の impl に降ろす
        return dispach.call_impl_method(typ, "Addable", "add", lv, rv)
        
    if isinstance(expr, CallExpr):
        return eval_call(expr, env, syms)

    raise EvalError(f"unsupported expr node: {expr!r}")


def eval_call(call: CallExpr, env: Dict[str, Cell], syms, outer: Optional[Dict[str, Cell]] = None):

    """
    if call.callee == "print" and len(call.args) == 0:
        return None     #Unit
    """
    
    # sigがない関数は呼べない
    if call.callee not in syms.sigs:
        raise EvalError(f"call to undeclared function '{call.callee}'")

    """
    if call.callee not in syms.funcs:
        raise EvalError(f"unknown function '{call.callee}'")
    """
    
    sig = syms.sigs[call.callee]
    
    #bound = bind_args(call, sig)
    #args = [eval_expr(bound[p.name], env, syms) for p in sig.params]

    # sigは引数名がないので、name args禁止
    if call.arg_style != "pos":
        return EvalError(
            f"argument count mismatch in call to {sig.name}: expected {len(sig.params)}, got {len(call.args)}"
        )
    
    # 引数を位置で評価
    arg_values = [eval_expr(a.expr, env=env, syms=syms) for a in call.args]

    dispatch = Dispatcher(syms)

    BUILTINS = {}
    BUILTINS.update(SURFACE_FUNCS)
    BUILTINS.update(BASE_FUNCS)
    # BUILTINS.update(CORE_FUNCS)

    # func実装があればそれを実行
    if call.callee in syms.funcs:
        
        fdecl = syms.funcs[call.callee]

        # func側の引数名で束縛（位置）
        if len(fdecl.params) != len(arg_values):
            # build_symbols/typecheck で弾かれる想定だが保険として
            raise EvalError(
                f"internal: func '{fdecl.name}' arity mismatch: "
                f"expected {len(fdecl.params)}, got {len(arg_values)}"
            )
        
        local: Dict[str, Cell] = {}

        for p, v in zip(fdecl.params, arg_values):
            local[p.name] = Cell(value=v, mutable=False)

        try:
            eval_block(fdecl.body, env=local, syms=syms, outer=outer)
            return None     # Unitの代替
        except ReturnSignal as rs:
            return rs.value
        except RaisedFailure as rf:
            # @attr.handledなら潰す
            attrs = syms.sig_attrs.get(call.callee, set())
            if "handled" in attrs:
                return None
            raise

    # func実装がなければ builtin を探す
    try:
        if call.callee in BUILTINS:
            return BUILTINS[call.callee](arg_values, dispatch)
        # 将来的に、syms.funcs や syms.impls を使って実装を解決する
        raise EvalError(f"function '{call.callee}' has no runtime implementation yet")
    
    except RaisedFailure as rf:
        # @attr.handlrdなら潰す
        attrs = syms.sig_attrs.get(call.callee, set())
        if "handled" in attrs:
            return None     # Unitに潰す
        raise

def eval_block(block: BlockStmt, env: Dict[str, Cell], syms, outer: Optional[Dict[str, Cell]] = None) -> None:
        
    for st in block.stmts:
        # return
        if isinstance(st, ReturnStmt):
            v = eval_expr(st.expr, env=env, syms=syms)
            raise ReturnSignal(v)
        
        # 今は ExprStmt のみ対応
        if isinstance(st, ExprStmt):
            eval_expr(st.expr, env=env, syms=syms, outer=outer)
            continue

        raise EvalError(f"unsupported statement in func body: {st!r}")