from ginger.errors import EvalError

def add(args, dispatch):
    
    if len(args) != 2:
        raise EvalError("internal error: add expects 2 args")
    
    a, b = args
    ta, tb = dispatch.type_of(a), dispatch.type_of(b)

    if ta != tb:
        raise EvalError(f"add requires same type, got {ta} and {tb}")
    
    return dispatch.call_impl_method(ta, "Addable", "add", a, b)

def breath(args, dispatch):

    if len(args) != 0:
        raise EvalError("internal error: breath expects 0 args")
    return dispatch.unit_value()
    
BASE_FUNCS = {
    "add": add,
    "breath": breath,
}