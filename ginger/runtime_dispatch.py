from .builtin import call_builtin, has_builtin
#from .eval import EvalError
from .errors import EvalError

def type_of(v):

    if isinstance(v, int): return "Int"
    if isinstance(v, float): return "Float"
    if isinstance(v, str): return "String"
    if v is None: return "Unit"
    raise EvalError(f"unknown runtime value type: {type(v)}")

class Dispatcher:

    def __init__(self, syms):
        self.syms = syms

    def call_impl_method(self, typ: str, guarantee: str, method: str, *args):
        
        key = (typ, guarantee, method)
        
        if key not in self.syms.impls:
            raise EvalError(f"missing impl: {typ} guarantees {guarantee}.{method}")
        
        builtin_id = self.syms.impls[key]

        if not has_builtin(builtin_id):
            raise EvalError(f"unknown builtin '{builtin_id}'")
        
        return call_builtin(builtin_id, *args)
    
    def type_of(self, v):
        return type_of(v)