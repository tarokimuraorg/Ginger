from typing import Dict, Union, Optional
from dataclasses import dataclass
from .args import bind_args
from ginger.surface.funcs import SURFACE_FUNCS
from .base.funcs import BASE_FUNCS
from ginger.runtime.dispatch import Dispatcher
from .symbols_builder import build_symbols
from .errors import EvalError, TypecheckError
from ginger.runtime.failures import RaisedFailure, FailureId
from .builtin import call_builtin

from .ast import (
    SigDecl,
    FuncDecl,
    VarDecl,
    AssignStmt,
    Expr,
    CallExpr,
    PosArg,
    NamedArg,
    IdentExpr,
    IntLit,
    FloatLit,
    BinaryExpr,
    BlockStmt,
    ReturnStmt,
    ExprStmt,
    TryStmt,
    CatchStmt,
    RequireGuarantees,
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
                eval_expr(item.expr, env=env, syms=syms, outer=None)
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

def eval_expr(expr: Expr, env: Dict[str, Cell], syms, outer: Optional[Dict[str, Cell]] = None) -> Value:

    if isinstance(expr, IntLit):
        return int(expr.value)

    if isinstance(expr, FloatLit):
        return float(expr.value)

    if isinstance(expr, IdentExpr):
        if expr.name in env:
            return env[expr.name].value
        if outer is not None and expr.name in outer:
            return outer[expr.name].value
        raise EvalError(f"unknown identifier '{expr.name}'")

    if isinstance(expr, BinaryExpr):
        raise TypecheckError("internal error: BinaryExpr should have been lowered to CallExpr")
        
    if isinstance(expr, CallExpr):
        return eval_call(expr, env, syms, outer=outer)

    raise EvalError(f"unsupported expr node: {expr!r}")

def _runtime_type(v):
    if isinstance(v, bool):
        return "Bool"
    if isinstance(v, int):
        return "Int"
    if isinstance(v, float):
        return "Float"
    if isinstance(v, str):
        return "String"
    if v is None:
        return "Unit"
    raise EvalError(f"unknown runtime value type: {type(v)}")

def eval_user_func(fname: str, args: list[Value], syms, caller_env: Dict[str, Cell]) -> Value:
    """
    Run a user-defined func body.
    - Parameters are bound positionally
    - Locals are immutable (mutable stmt not implemented inside func yet).
    - Global lookup is allowed via 'caller_env' as 'outer'.
    - ReturnSignal carries the return value.
    - If a RaisedFailure happens and the corresponding sig has @attr.handled, swallow it and return Unit(None).
    """
    if fname not in syms.funcs:
        raise EvalError(f"unknown func '{fname}'")
    
    fdecl: FuncDecl = syms.funcs[fname]

    if len(args) != len(fdecl.params):
        raise EvalError(
            f"argument count mismatch in call to {fname}: expected {len(fdecl.params)}, got {len(args)}"
        )
    
    # local env: bind_parameters
    local: Dict[str, Cell] = {}

    for p,v in zip(fdecl.params, args):
        local[p.name] = Cell(value=v, mutable=False)

    try:
        eval_block(fdecl.body, env=local, syms=syms, outer=caller_env)
        return None     # implicit Unit
    except ReturnSignal as rs:
        return rs.value
    except RaisedFailure:
        # @attr.handled: swallow failures of this sig
        attrs = syms.sig_attrs.get(fname, set())
        if "handled" in attrs:
            return None
        raise


def eval_call(expr: CallExpr, env: Dict[str, Cell], syms, outer: Optional[Dict[str, Cell]] = None):

    # 引数評価
    if expr.arg_style != "pos":
        raise EvalError(f"named args not supported at runtime for '{expr.callee}'")
    
    args = [eval_expr(a.expr, env, syms, outer) for a in expr.args]

    # user func があればそちらで対応（既存の仕様があれば維持）
    if expr.callee in syms.funcs:
        return eval_user_func(expr.callee, args, syms, env)
    
    # sig 呼び出しなら impl 経由で builtin に落とす
    if expr.callee in syms.sigs:
        
        sig = syms.sigs[expr.callee]
        req_guars = [r for r in sig.requires if isinstance(r, RequireGuarantees)]

        if len(req_guars) != 1:
            raise EvalError(
                f"sig '{sig.name}' must have exactly 1 'require T guarantees G' for runtime dispatch (got {len(req_guars)})"
            )
        
        guar = req_guars[0].guarantee_name

        # Self の具象型を引数から決める（四則は左右同型を想定）
        if not args:
            raise EvalError(f"sig '{sig.name}' needs args for runtime dispatch")
        
        t0 = _runtime_type(args[0])
        key = (t0, guar, sig.name)      # (Type, Guarantee, Method)
        builtin_id = syms.impls.get(key)

        if builtin_id is None:
            raise EvalError(f"no impl for {t0} guarantees {guar}.{sig.name}")
        
        # builtin 実行
        try:
            return call_builtin(builtin_id, *args)
        except ZeroDivisionError:
            raise RaisedFailure(FailureId.DivideByZero)
    
    raise EvalError(f"function '{expr.callee}' has no runtime implementation yet")
    
    """
    # sigがない関数は呼べない
    if call.callee not in syms.sigs:
        raise EvalError(f"call to undeclared function '{call.callee}'")

    sig = syms.sigs[call.callee]
    
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
    """


def eval_block(block: BlockStmt, env: Dict[str, Cell], syms, outer: Optional[Dict[str, Cell]] = None) -> None:
        
    for st in block.stmts:
        # return
        if isinstance(st, ReturnStmt):
            v = eval_expr(st.expr, env=env, syms=syms, outer=outer)
            raise ReturnSignal(v)
        
        # 今は ExprStmt のみ対応
        if isinstance(st, ExprStmt):
            eval_expr(st.expr, env=env, syms=syms, outer=outer)
            continue

        raise EvalError(f"unsupported statement in func body: {st!r}")