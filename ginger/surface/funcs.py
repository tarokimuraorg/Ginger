from ginger.errors import EvalError
from ginger.runtime_failures import RaisedFailure
from ginger.core.failure_spec import FailureId

"""
def _unit(dispatch):
    # Unitの表現が環境次第なので安全側に寄せる
    if hasattr(dispatch, "unit"):
        return dispatch.unit()
    if hasattr(dispatch, "Unit"):
        return dispatch.Unit()
    return None
"""

def print(args, dispatch):

    # 引数：0個
    if len(args) == 0:
        return None     # Unit
    
    # 引数：1個（2個以上ある場合は先頭のみ採用）
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