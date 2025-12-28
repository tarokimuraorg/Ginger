from .parser import parse
from .typecheck import typecheck_program
from .eval import eval_program

def compile(src: str):
    prog = parse(src)
    typecheck_program(prog)
    return prog

def execute(prog):
    eval_program(prog)

def run(src: str):
    execute(compile(src))