from .errors import EvalError
from .runtime_failures import RaisedFailure
from ginger.core.failure_spec import FailureId

def print(args, dispatch):

    if len(args) != 1:
        raise EvalError("print expects one argument")
    
    v = args[0]
    typ = dispatch.type_of(v) if hasattr(dispatch, "type_of") else None

    try:
        return dispatch.call_impl_method(typ, "Printable", "print", v)
    except EvalError as e:
        # Printable 未実装、型不一致、IO系など「printの失敗」は PrintErr に包む
        raise RaisedFailure(FailureId("PrintErr")) from e


SURFACE_FUNCS = {
    "print": print,
}