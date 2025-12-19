#from .builtin import call_builtin
from .errors import EvalError
#from .runtime_dispatch import type_of

"""
def type_of(v):
    if isinstance(v, int): return "Int"
    if isinstance(v, float): return "Float"
    if isinstance(v, str): return "String"
    if v is None: return "Unit"
    raise EvalError(f"unknown runtime value type: {type(v)}")
"""
"""
def call_impl_method(syms, typ: str, guarantee: str, method: str, receiver):
    key = (typ, guarantee, method)
    if key not in syms.impls:
        raise EvalError(f"missing impl: {typ} guarantees {guarantee}.{method}")
    builtin_name = syms.impls[key]
    return call_builtin(builtin_name, receiver)
"""

def surface_print(args, dispatch):

    if len(args) != 1:
        raise EvalError("print expects one argument")
    
    v = args[0]
    typ = dispatch.type_of(v) if hasattr(dispatch, "type_of") else None

    return dispatch.call_impl_method(typ, "Printable", "print", v)

SURFACE_FUNCS = {
    "print": surface_print,
}