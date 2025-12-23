from typing import Callable, Dict, Any

# いまのランタイム値（必要なら ginger/eval.py 側の Value と合わせる）
Value = Any
BuiltinFn = Callable[..., Value]

BUILTINS: Dict[str, BuiltinFn] = {
    "core.int.add":   lambda a, b: a + b,
    "core.float.add": lambda a, b: a + b,
    "core.int.print":     lambda x: (print(x), None)[1],  # Unit は None 表現
    "core.float.print": lambda x: (print(x), None)[1],
    "core.string.print": lambda x: (print(x), None)[1],
    # 比較を入れるならここに：
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