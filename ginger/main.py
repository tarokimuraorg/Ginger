from pathlib import Path
from ginger.pipeline import run

def main() -> None:

    root = Path(__file__).parent
    src = (root / "script/Scene_7.ginger").read_text(encoding="utf-8")
    
    # 実行
    run(src)
    
if __name__ == "__main__":
    main()