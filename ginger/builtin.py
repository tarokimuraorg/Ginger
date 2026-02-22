from typing import Callable, Dict, Any, Literal, Tuple

# いまのランタイム値（必要なら ginger/eval.py 側の Value と合わせる）
Value = Any
BuiltinFn = Callable[..., Value]

# Ordering の最小表現（型タグ付き）
OrderingTag = Literal["Left", "Flat", "Right"]
OrderingValue = Tuple[str, OrderingTag]     # ("Ordering", tag)

def ordering(tag: OrderingTag) -> OrderingValue:
    return ("Ordering", tag)

BUILTINS: Dict[str, BuiltinFn] = {

    "core.int.add":   lambda a, b: a + b,
    "core.float.add": lambda a, b: a + b,

    "core.int.sub":   lambda a, b: a - b,
    "core.float.sub": lambda a, b: a - b,

    "core.int.mul":   lambda a, b: a * b,
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
    if isinstance(a, int):
        return BUILTINS["core.int.cmp"](a, b)
    if isinstance(a, float):
        return BUILTINS["core.float.cmp"](a, b)
    raise TypeError("unsupported type for cmp")