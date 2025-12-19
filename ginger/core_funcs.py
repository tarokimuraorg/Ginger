#from .eval import EvalError
#from .builtin import type_of, call_builtin, has_builtin
from .errors import EvalError

def core_add(args, dispatch):
    
    if len(args) != 2:
        raise EvalError("internal error: add expects 2 args")
    
    a, b = args
    ta, tb = dispatch.type_of(a), dispatch.type_of(b)

    if ta != tb:
        raise EvalError(f"add requires same type, got {ta} and {tb}")
    
    return dispatch.call_impl_method(ta, "Addable", "add", a, b)
    
    """
    key = (ta, "Addable", "add")

    if key not in syms.impls:
        raise EvalError(f"missing impl for type '{ta}' guarantee 'Addable' method 'add'")
    
    builtin_id = syms.impls[key]

    if not has_builtin(builtin_id):
        raise EvalError(f"unknown builtin '{builtin_id}'")
    
    return call_builtin(builtin_id, a, b)
    """
    
CORE_FUNCS = {"add": core_add}