from dataclasses import dataclass
from typing import List, Literal

Level = Literal["warning", "note"]

@dataclass(frozen=True)
class Diagnostic:
    level: Level
    code: str
    message: str

class Diagnostics:

    def __init__(self) -> None:
        self.items: List[Diagnostic] = []

    def warn(self, code: str, message: str) -> None:
        self.items.append(Diagnostic("warning", code, message))

    def __iter__(self):
        return iter(self.items)