from ginger.core import io
from ginger.core import operator

def prelude_items():
    items = []
    items += operator.core_items()
    items += io.core_items()
    return items