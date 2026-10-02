from ginger.errors import EvalError
from ginger.runtime.thunk import ThunkValue

def type_of(v, actual_type=None):

    if actual_type is not None:
        return actual_type.name
    if isinstance(v, bool): return "Bool"
    if isinstance(v, int):
        raise EvalError("integer dispatch requires an explicit Ginger TypeRef")
    if isinstance(v, float): return "Float"
    if isinstance(v, str): return "String"
    if isinstance(v, ThunkValue): return "Thunk"
    if v is None: return "Unit"
    raise EvalError(f"unknown runtime value type: {type(v)}")
