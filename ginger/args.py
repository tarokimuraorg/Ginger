from dataclasses import dataclass
from typing import Dict
from .ast import CallExpr, SigDecl, FuncDecl, PosArg, NamedArg, Expr

@dataclass
class BindError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message

# =====================
# Arg binding (positional vs named)
# =====================

def bind_args(call: CallExpr, sig: SigDecl) -> Dict[str, Expr]:
    """
    Returns dict[param_name -> Expr]
    """
    params = sig.params
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