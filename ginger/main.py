from pathlib import Path
from ginger.pipeline import run

def main() -> None:

    root = Path(__file__).parent
    scene_id = 9
    src = (root / f"script/Scene_{scene_id}.ginger").read_text(encoding="utf-8")
    
    # 実行
    run(src)
    
if __name__ == "__main__":
    main()