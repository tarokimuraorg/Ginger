from pathlib import Path
from ginger.pipeline import run


def main() -> None:

    root = Path(__file__).parent

    catalog = (root / "Catalog.ginger").read_text(encoding="utf-8")
    impl    = (root / "Impl.ginger").read_text(encoding="utf-8")
    code    = (root / "Code.ginger").read_text(encoding="utf-8")

    # 単純結合（重複チェックは typecheck 側で行う）
    src = "\n\n".join([catalog, impl, code])
    run(src)
    
    
if __name__ == "__main__":
    main()