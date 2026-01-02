from pathlib import Path
from ginger.pipeline import run
from ginger.parser import parse
from ginger.typecheck import typecheck_program
from ginger.eval import eval_program


def main() -> None:

    root = Path(__file__).parent
    #src = (root / "script/Scene_1.ginger").read_text(encoding="utf-8")
    src = """
    var x: Int = 1 + 2
    x = x + 3
    print(x)
    try print(div(1.0,0.0))
    catch DivideByZero print(999)
    """
    
    # 実行
    run(src)

    # prog = parse(src)
    # typecheck_program(prog)
    # eval_program(prog)
    
    # catalog = (root / "Catalog.ginger").read_text(encoding="utf-8")
    # impl    = (root / "Impl.ginger").read_text(encoding="utf-8")
    # code    = (root / "Code.ginger").read_text(encoding="utf-8")

    # 単純結合（重複チェックは typecheck 側で行う）
    # src = "\n\n".join([catalog, impl, code])

    
if __name__ == "__main__":
    main()