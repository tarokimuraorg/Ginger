from pathlib import Path
from ginger.pipeline import run
from ginger.parser import parse
from ginger.typecheck import typecheck_program
from ginger.eval import eval_program


def main() -> None:

    src = """
    var x: Int = 1 + 2
    print(x)

    let y: Int = 1 + 2
    print(y)
    """

    prog = parse(src)
    typecheck_program(prog)
    eval_program(prog)

    #root = Path(__file__).parent

    #catalog = (root / "Catalog.ginger").read_text(encoding="utf-8")
    #impl    = (root / "Impl.ginger").read_text(encoding="utf-8")
    #code    = (root / "Code.ginger").read_text(encoding="utf-8")

    # 単純結合（重複チェックは typecheck 側で行う）
    #src = "\n\n".join([catalog, impl, code])

    # 実行
    #run(src)
    
    
if __name__ == "__main__":
    main()