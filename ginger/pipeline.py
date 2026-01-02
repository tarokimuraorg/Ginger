from .parser import parse
from .lower import lower_program
from .typecheck import typecheck_program
from .eval import eval_program
from .diagnostics import Diagnostics

def compile(src: str):

    prog = parse(src)
    prog = lower_program(prog)
    diags = Diagnostics()

    typecheck_program(prog, diags)

    # warning をまとめて表示
    for d in diags:
        if d.level == "warning":
            print(f"warning[{d.code}]: {d.message}")

    return prog

def execute(prog):
    eval_program(prog)

def run(src: str):
    execute(compile(src))