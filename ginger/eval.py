from typing import Dict, Union, Optional, Any
from dataclasses import dataclass
from .args import bind_args
from .ast import TypeRef
from .numeric import widen_value
from .symbols_builder import build_symbols, normalize_types, ResolvedCall
from .typecheck import typecheck_program, resolve_typeref
from .diagnostics import Diagnostics
from .errors import EvalError, TypecheckError
from ginger.runtime.failures import RaisedFailure, FailureId
from .builtin import call_builtin
from ginger.runtime.thunk import ThunkValue

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
    Int64Lit,
    FloatLit,
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
#Value = Union[int, float]
Value = Any

@dataclass
class Cell:
    value: Value
    mutable: bool   # let=False, var=True
    typ: Optional[TypeRef] = None

class FunctionEnv(dict):
    """Call-local type substitution, also preserved by existing Thunk snapshots."""
    def __init__(self, type_bindings, values=()):
        super().__init__(values)
        self.type_bindings = type_bindings

    def copy(self):
        return FunctionEnv(self.type_bindings, self)


def instantiated_type(typ, env):
    return resolve_typeref(typ, getattr(env, "type_bindings", {}))


@dataclass
class ReturnSignal(Exception):
    value: "Value"
    typ: TypeRef

def eval_program(prog) -> Dict[str, Cell]:
    
    syms = build_symbols(prog)
    prog = normalize_types(prog, syms.failuresets)
    # Keep the exact checked expressions alive for runtime boundary/dispatch types.
    typecheck_program(prog, Diagnostics(), syms=syms)
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

                        # Handler failures escape this try, including sibling catches.
                        eval_expr(c.expr, env=env, syms=syms)
                        break
                
                if not handled:
                    raise   # 一致する catch が無ければ外へ
            
            i = j
            continue

        # catch単体は実行時もエラーにしておく
        if isinstance(item, CatchStmt):
            raise EvalError("catch without preceding try")
            
        if isinstance(item, (VarDecl, AssignStmt)):
            eval_binding_statement(item, env, syms)
            i += 1
            continue

        if isinstance(item, ExprStmt):
            eval_expr(item.expr, env=env, syms=syms)
            i += 1
            continue

        i += 1

    return env


def eval_binding_statement(stmt, env, syms, outer=None):
    """Evaluate/coerce before publishing a new Cell, including on reassignment."""
    if isinstance(stmt, VarDecl):
        typ, mutable = instantiated_type(stmt.typ, env), stmt.mutable
    else:
        if stmt.name not in env:
            raise EvalError(f"unknown identifier '{stmt.name}'")
        cell = env[stmt.name]
        if not cell.mutable:
            raise EvalError(f"cannot assign to immutable binding '{stmt.name}'")
        typ, mutable = cell.typ, cell.mutable
    value = eval_expr(stmt.expr, env, syms, outer)
    value = widen_value(value, expression_type(stmt.expr, env, syms, outer), typ)
    env[stmt.name] = Cell(value=value, mutable=mutable, typ=typ)

def eval_expr(expr: Expr, env: Dict[str, Cell], syms, outer: Optional[Dict[str, Cell]] = None) -> Value:

    if isinstance(expr, (IntLit, Int64Lit)):
        return int(expr.value)

    if isinstance(expr, FloatLit):
        return float(expr.value)

    if isinstance(expr, IdentExpr):
        if expr.name in env:
            return env[expr.name].value
        if outer is not None and expr.name in outer:
            return outer[expr.name].value
        raise EvalError(f"unknown identifier '{expr.name}'")

    if isinstance(expr, CallExpr):
        return eval_call(expr, env, syms, outer=outer)

    raise EvalError(f"unsupported expr node: {expr!r}")

def expression_type(expr, env, syms, outer=None):
    # Generic parameters are instantiated in Cells at the call boundary.
    if isinstance(expr, IdentExpr):
        cell = env.get(expr.name)
        if cell is None and outer is not None:
            cell = outer.get(expr.name)
        if cell is not None:
            return cell.typ
    return instantiated_type(syms.expression_types[id(expr)], env)


def eval_user_func(fname: str, args: list[Value], syms, caller_env: Dict[str, Cell], resolved: ResolvedCall) -> Value:
    """
    Run a user-defined func body.
    - Parameters are bound positionally
    - Parameters are immutable; local declarations retain their let/var mutability.
    - Global lookup is allowed via 'caller_env' as 'outer'.
    - ReturnSignal carries the return value.
    """
    if fname not in syms.funcs:
        raise EvalError(f"unknown func '{fname}'")
    
    fdecl: FuncDecl = syms.funcs[fname]

    if len(args) != len(fdecl.params):
        raise EvalError(
            f"argument count mismatch in call to {fname}: expected {len(fdecl.params)}, got {len(args)}"
        )
    
    # local env: bind_parameters
    local = FunctionEnv(resolved.type_bindings)

    for p, v, typ in zip(fdecl.params, args, resolved.parameter_types):
        local[p.name] = Cell(value=v, mutable=False, typ=typ)

    try:
        eval_block(fdecl.body, env=local, syms=syms, outer=caller_env)
        return None     # implicit Unit
    except ReturnSignal as rs:
        return widen_value(rs.value, rs.typ, resolved.return_type)


def eval_call(expr: CallExpr, env: Dict[str, Cell], syms, outer: Optional[Dict[str, Cell]] = None):

    # 引数評価
    if expr.arg_style != "pos":
        raise EvalError(f"named args not supported at runtime for '{expr.callee}'")
    
    # --- thunk (遅延)---
    if expr.callee == "thunk":
        if len(expr.args) != 1:
            raise EvalError("thunk expects exactly 1 argument")
        arg_expr = expr.args[0].expr
        return ThunkValue(arg_expr, env.copy(), outer)
    
    # --- force (強制実行)---
    if expr.callee == "force":
        if len(expr.args) != 1:
            raise EvalError("force expects exactly 1 argument")
        v = eval_expr(expr.args[0].expr, env, syms, outer)
        if not isinstance(v, ThunkValue):
            raise EvalError("force expects Thunk")
        return eval_expr(v.expr, v.env, syms, v.outer)

    args = [eval_expr(a.expr, env, syms, outer) for a in expr.args]

    actual_types = [expression_type(a.expr, env, syms, outer) for a in expr.args]
    checked = syms.resolved_calls[id(expr)]
    # Only substitute already inferred symbolic types for a generic function
    # frame. Never infer again from runtime values or from the caller's target.
    resolved = ResolvedCall(
        {name: instantiated_type(typ, env) for name, typ in checked.type_bindings.items()},
        tuple(instantiated_type(typ, env) for typ in checked.parameter_types),
        instantiated_type(checked.return_type, env),
        checked.implementation,
    )
    args = [widen_value(value, actual, parameter)
            for value, actual, parameter in zip(args, actual_types, resolved.parameter_types)]

    if expr.callee in syms.funcs:
        return eval_user_func(expr.callee, args, syms, env, resolved)

    if resolved.implementation is None:
        raise EvalError(f"function '{expr.callee}' has no resolved runtime implementation")
    try:
        return call_builtin(resolved.implementation, *args)
    except ZeroDivisionError:
        raise RaisedFailure(FailureId.DivideByZero)


def eval_block(block: BlockStmt, env: Dict[str, Cell], syms, outer: Optional[Dict[str, Cell]] = None) -> None:
        
    for st in block.stmts:
        if isinstance(st, (VarDecl, AssignStmt)):
            eval_binding_statement(st, env, syms, outer)
            continue

        # return
        if isinstance(st, ReturnStmt):
            v = eval_expr(st.expr, env=env, syms=syms, outer=outer)
            raise ReturnSignal(v, expression_type(st.expr, env, syms, outer))
        
        # Expression statements retain their existing behavior.
        if isinstance(st, ExprStmt):
            eval_expr(st.expr, env=env, syms=syms, outer=outer)
            continue

        raise EvalError(f"unsupported statement in func body: {st!r}")
