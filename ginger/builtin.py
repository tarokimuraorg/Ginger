from typing import Callable, Dict, Any, Literal, Tuple
from .numeric import INT_MIN, INT_MAX
from .core.failure_spec import FailureId, failures
from .runtime.failures import RaisedFailure

# Managed implementation contracts, specialized after concrete impl dispatch.
# Custom builtin sigs remain trusted declarations; this is not Python effect inference.
INT_ARITHMETIC_FAILURES = {
    f"core.int.{name}": failures(FailureId.IntegerOverflow)
    for name in ("add", "sub", "mul")
}


def checked_int_result(result: int) -> int:
    if not INT_MIN <= result <= INT_MAX:
        raise RaisedFailure(FailureId.IntegerOverflow)
    return result

# いまのランタイム値（必要なら ginger/eval.py 側の Value と合わせる）
Value = Any
BuiltinFn = Callable[..., Value]

# Ordering の最小表現（型タグ付き）
OrderingTag = Literal["Left", "Flat", "Right"]
OrderingValue = Tuple[str, OrderingTag]     # ("Ordering", tag)

def ordering(tag: OrderingTag) -> OrderingValue:
    return ("Ordering", tag)

BUILTINS: Dict[str, BuiltinFn] = {

    "core.int.add":   lambda a, b: checked_int_result(a + b),
    "core.float.add": lambda a, b: a + b,

    "core.int.sub":   lambda a, b: checked_int_result(a - b),
    "core.float.sub": lambda a, b: a - b,

    "core.int.mul":   lambda a, b: checked_int_result(a * b),
    "core.float.mul": lambda a, b: a * b,

    "core.float.div": lambda a, b: a / b,

    "core.int.neg": lambda a: -a,
    "core.float.neg": lambda a:-a,
    
    "core.int.toFloat": lambda a: float(a),

    "core.int.print":     lambda x: (print(x), None)[1],  # Unit は None 表現
    "core.float.print": lambda x: (print(x), None)[1],
    "core.string.print": lambda x: (print(x), None)[1],
    "core.ordering.print": lambda o: (print(o[1]), None)[1],
    "core.bool.print": lambda x: (print(str(x).lower()), None)[1],

    # --- cmp (Ordering) ---
    "core.int.cmp": lambda a, b: ordering("Left") if a > b else ordering("Flat") if a == b else ordering("Right"),
    "core.float.cmp": lambda a, b: ordering("Left") if a > b else ordering("Flat") if a == b else ordering("Right"),

    # --- eq (via cmp) ---
    "core.int.eq":   lambda a, b: (_cmp_result(a, b)[1] == "Flat"),
    "core.float.eq": lambda a, b: (_cmp_result(a, b)[1] == "Flat"),

    # --- lt (via cmp) ---
    "core.int.lt":   lambda a, b: (_cmp_result(a, b)[1] == "Right"),
    "core.float.lt": lambda a, b: (_cmp_result(a, b)[1] == "Right"),

}

def has_builtin(builtin_id: str) -> bool:
    return builtin_id in BUILTINS

def call_builtin(builtin_id: str, *args: Value) -> Value:
    try:
        fn = BUILTINS[builtin_id]
    except KeyError as e:
        raise KeyError(f"unknown builtin '{builtin_id}'") from e
    return fn(*args)

def _cmp_result(a, b):
    # Numeric implementations share value comparison, not Ginger type inference.
    return ordering("Left") if a > b else ordering("Flat") if a == b else ordering("Right")
