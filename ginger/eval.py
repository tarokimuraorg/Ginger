from typing import Dict, Union, Optional, Any
from dataclasses import dataclass
from contextvars import ContextVar
from .args import bind_args
from .ast import TypeRef
from .numeric import widen_value
from .symbols_builder import build_symbols, normalize_types, ResolvedCall
from .typecheck import typecheck_program, resolve_typeref
from .diagnostics import Diagnostics
from .errors import EvalError, TypecheckError
from .builtin import builtin_failure_contract
from ginger.runtime.thunk import ThunkValue
from ginger.runtime.context import RuntimeContext
from ginger.runtime.builtin_bridge import invoke_builtin
from ginger.runtime.catches import handle_try_events
from ginger.runtime.results import EvalResult, NoValue, Value as ProducedValue

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

@dataclass(frozen=True)
class UninitializedBinding:
    """A declared binding without a value; retains the original failure IDs."""
    mutable: bool
    typ: TypeRef
    related_event_ids: tuple[int, ...]

Binding = Cell | UninitializedBinding


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
    result: EvalResult
    typ: TypeRef

_statement_scope: ContextVar[str] = ContextVar("ginger_statement_scope", default="<program>")

_active_runtime: ContextVar[RuntimeContext] = ContextVar("ginger_runtime")


def eval_program(prog) -> Dict[str, Binding]:
    return _eval_program_with_context(prog, RuntimeContext())


def _eval_program_with_context(prog, context: RuntimeContext) -> Dict[str, Binding]:
    """Internal injectable entry; public results remain an environment dictionary.

    Root and user calls retain their own frames and share one history.
    ContextVar scopes recursive evaluation without changing FunctionEnv or
    capturing runtime state in Thunk environments. Always restore on failure.
    """
    token = _active_runtime.set(context)
    scope_token = _statement_scope.set("<program>")
    try:
        with context.call("<program>"):
            return _eval_program(prog)
    finally:
        _statement_scope.reset(scope_token)
        _active_runtime.reset(token)


def _eval_program(prog) -> Dict[str, Binding]:
    
    syms = build_symbols(prog)
    prog = normalize_types(prog, syms.failuresets)
    # Keep the exact checked expressions alive for runtime boundary/dispatch types.
    typecheck_program(prog, Diagnostics(), syms=syms)
    env: Dict[str, Binding] = {}

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
            
            context = _active_runtime.get()
            first_new_event_id = context.next_event_id
            result = eval_expr(item.expr, env=env, syms=syms, outer=None)
            record_incomplete(item, i, result)
            # IDs are monotonic per context. Snapshot before executing handlers;
            # an old NoValue cause is not a new occurrence in this try.
            targets = tuple(event.event_id for event in context.failure_history
                            if event.event_id >= first_new_event_id)

            def handler(catch, index):
                handler_result = eval_expr(catch.expr, env=env, syms=syms)
                record_incomplete(catch, index, handler_result)
                return handler_result

            handle_try_events(context, targets, [
                (c.failure_name, lambda c=c, index=index: handler(c, index))
                for index, c in enumerate(catches, start=i + 1)])

            i = j
            continue

        # catch単体は実行時もエラーにしておく
        if isinstance(item, CatchStmt):
            raise EvalError("catch without preceding try")
            
        if isinstance(item, (VarDecl, AssignStmt)):
            record_incomplete(item, i, eval_binding_statement(item, env, syms))
            i += 1
            continue

        if isinstance(item, ExprStmt):
            record_incomplete(item, i, eval_expr(item.expr, env=env, syms=syms))
            i += 1
            continue

        i += 1

    return env


def record_incomplete(stmt, index, result):
    if isinstance(result.value_result, NoValue):
        context = _active_runtime.get()
        call_id = context.current_call_id
        context.record_incomplete(call_id, _statement_scope.get(), index,
                                  type(stmt).__name__, result.related_event_ids)


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
    result = eval_expr(stmt.expr, env, syms, outer)
    if isinstance(result.value_result, NoValue):
        if isinstance(stmt, VarDecl):
            env[stmt.name] = UninitializedBinding(mutable, typ, result.related_event_ids)
        return result
    value = widen_value(result.value_result.value, expression_type(stmt.expr, env, syms, outer), typ)
    env[stmt.name] = Cell(value=value, mutable=mutable, typ=typ)
    return EvalResult(ProducedValue(None), result.related_event_ids)

def eval_expr(expr: Expr, env: Dict[str, Binding], syms, outer: Optional[Dict[str, Binding]] = None) -> EvalResult:

    if isinstance(expr, (IntLit, Int64Lit)):
        return EvalResult(ProducedValue(int(expr.value)))

    if isinstance(expr, FloatLit):
        return EvalResult(ProducedValue(float(expr.value)))

    if isinstance(expr, IdentExpr):
        binding = env.get(expr.name)
        if binding is None and outer is not None:
            binding = outer.get(expr.name)
        if isinstance(binding, UninitializedBinding):
            return EvalResult(NoValue(), binding.related_event_ids)
        if binding is not None:
            return EvalResult(ProducedValue(binding.value))
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


def eval_user_func(fname: str, args: list[Value], syms, caller_env: Dict[str, Binding], resolved: ResolvedCall) -> EvalResult:
    """
    Run a user-defined func body.
    - Parameters are bound positionally
    - Parameters are immutable; local declarations retain their let/var mutability.
    - Global lookup is allowed via 'caller_env' as 'outer'.
    - ReturnSignal ends the body, including a failed return until Phase 9.
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

    context = _active_runtime.get()
    with context.call(fname, syms.sig_failures[fname]) as frame:
        token = _statement_scope.set(fname)
        try:
            try:
                result = eval_block(fdecl.body, env=local, syms=syms, outer=caller_env)
            except ReturnSignal as rs:
                result = rs.result
                if isinstance(result.value_result, ProducedValue):
                    result = EvalResult(ProducedValue(widen_value(result.value_result.value, rs.typ,
                                                                 resolved.return_type)),
                                        result.related_event_ids)
                # Failed returns still end the body until Phase 9.
            ids = tuple(dict.fromkeys((*result.related_event_ids,
                                       *context.unresolved_pending(frame.call_id))))
            return EvalResult(result.value_result, ids)
        finally:
            _statement_scope.reset(token)


def eval_call(expr: CallExpr, env: Dict[str, Binding], syms, outer: Optional[Dict[str, Binding]] = None):

    # 引数評価
    if expr.arg_style != "pos":
        raise EvalError(f"named args not supported at runtime for '{expr.callee}'")
    
    # --- thunk (遅延)---
    if expr.callee == "thunk":
        if len(expr.args) != 1:
            raise EvalError("thunk expects exactly 1 argument")
        arg_expr = expr.args[0].expr
        return EvalResult(ProducedValue(ThunkValue(arg_expr, env.copy(), outer)))
    
    # --- force (強制実行)---
    if expr.callee == "force":
        if len(expr.args) != 1:
            raise EvalError("force expects exactly 1 argument")
        result = eval_expr(expr.args[0].expr, env, syms, outer)
        if isinstance(result.value_result, NoValue):
            return result
        v = result.value_result.value
        if not isinstance(v, ThunkValue):
            raise EvalError("force expects Thunk")
        forced = eval_expr(v.expr, v.env, syms, v.outer)
        return EvalResult(forced.value_result, tuple(dict.fromkeys(
            (*result.related_event_ids, *forced.related_event_ids))))

    args = []
    related_ids = []
    for argument in expr.args:
        result = eval_expr(argument.expr, env, syms, outer)
        related_ids.extend(result.related_event_ids)
        if isinstance(result.value_result, NoValue):
            return EvalResult(NoValue(), tuple(dict.fromkeys(related_ids)))
        args.append(result.value_result.value)

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
        result = eval_user_func(expr.callee, args, syms, env, resolved)
        return EvalResult(result.value_result, tuple(dict.fromkeys((*related_ids, *result.related_event_ids))))

    if resolved.implementation is None:
        raise EvalError(f"function '{expr.callee}' has no resolved runtime implementation")
    sig = syms.sigs[expr.callee]
    contract = builtin_failure_contract(syms.sig_failures[expr.callee],
                                       resolved.implementation,
                                       direct_builtin=sig.builtin is not None)
    context = _active_runtime.get()
    result = invoke_builtin(resolved.implementation, args, contract, context, context.current_call_id)
    return EvalResult(result.value_result, tuple(dict.fromkeys((*related_ids, *result.related_event_ids))))


def eval_block(block: BlockStmt, env: Dict[str, Binding], syms, outer: Optional[Dict[str, Binding]] = None) -> EvalResult:
    for index, st in enumerate(block.stmts):
        if isinstance(st, (VarDecl, AssignStmt)):
            result = eval_binding_statement(st, env, syms, outer)
        elif isinstance(st, ReturnStmt):
            result = eval_expr(st.expr, env=env, syms=syms, outer=outer)
            record_incomplete(st, index, result)
            # Failed returns still end this body. No fabricated value, and no
            # execution of statically unreachable statements before Phase 9.
            raise ReturnSignal(result, expression_type(st.expr, env, syms, outer))
        elif isinstance(st, ExprStmt):
            result = eval_expr(st.expr, env=env, syms=syms, outer=outer)
        else:
            raise EvalError(f"unsupported statement in func body: {st!r}")
        record_incomplete(st, index, result)
    # Reaching the end is normal Unit completion, independent of pending failures.
    return EvalResult(ProducedValue(None))
