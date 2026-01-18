from pathlib import Path
from ginger.core.catalog_loader import load_core_catalog_json

def _core_items(name: str):
    root = Path(__file__).parents[1]
    catalog = root / "catalog" / f"{name}.json"
    return load_core_catalog_json(catalog)

def prelude_items():
    items = []
    items += _core_items("math")
    items += _core_items("cast")
    items += _core_items("ordering")
    items += _core_items("io")
    return items