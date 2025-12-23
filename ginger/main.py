from pathlib import Path
from ginger.parser import parse
from ginger.typecheck import typecheck_program
from ginger.eval import eval_program


def main() -> None:

    root = Path(__file__).parent

    catalog = (root / "Catalog.ginger").read_text(encoding="utf-8")
    impl    = (root / "Impl.ginger").read_text(encoding="utf-8")
    code    = (root / "Code.ginger").read_text(encoding="utf-8")

    print("Loaded:", catalog, impl, code)

    # 単純結合（重複チェックは typecheck 側で行う）
    src = "\n\n".join([catalog, impl, code])
    prog = parse(src)
    print("Items:", len(prog.items))
    print("Top:", [type(x).__name__ for x in prog.items[:10]])
    type_env = typecheck_program(prog)

    print("== typecheck ==")

    for k, v in type_env.items():
        print(f"  {k}: {v}")

    print("\n== run Code.ginger ==")

    eval_program(prog)
    

if __name__ == "__main__":
    main()