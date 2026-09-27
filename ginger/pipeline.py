import sys
from .parser import parse
from .lower import lower_program
from .typecheck import typecheck_program
from .eval import eval_program
from .diagnostics import Diagnostics
from .runtime.results import ExecutionResult

def compile(src: str):

    prog = parse(src)
    prog = lower_program(prog)
    diags = Diagnostics()

    typecheck_program(prog, diags)

    # warning をまとめて表示
    for d in diags:
        if d.level == "warning":
            print(f"warning[{d.code}]: {d.message}", file=sys.stderr)

    return prog

def execute(prog) -> ExecutionResult:
    return eval_program(prog)

def run(src: str) -> ExecutionResult:
    return execute(compile(src))
