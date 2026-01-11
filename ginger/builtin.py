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

    # --- cmp (Ordering) ---
    "core.int.cmp": lambda a, b: ordering("Left") if a > b else ordering("Flat") if a == b else ordering("Right"),
    "core.float.cmp": lambda a, b: ordering("Left") if a > b else ordering("Flat") if a == b else ordering("Right"),

    # "core.int.eq":    lambda a, b: a == b,
    # "core.int.lt":    lambda a, b: a < b,
    # "core.int.gt":    lambda a, b: a > b,
    # "core.float.eq":  lambda a, b: a == b,
    # "core.float.lt":  lambda a, b: a < b,
    # "core.float.gt":  lambda a, b: a > b,
}

def has_builtin(builtin_id: str) -> bool:
    return builtin_id in BUILTINS

def call_builtin(builtin_id: str, *args: Value) -> Value:
    try:
        fn = BUILTINS[builtin_id]
    except KeyError as e:
        raise KeyError(f"unknown builtin '{builtin_id}'") from e
    return fn(*args)