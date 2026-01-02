from dataclasses import dataclass
from typing import List, Literal, Optional

Level = Literal["warning", "note"]

@dataclass(frozen=True)
class Diagnostic:
    level: Level
    code: str
    message: str
    pos: Optional[int] = None   # 位置情報が取れるようになったら使う

class Diagnostics:

    def __init__(self) -> None:
        self.items: List[Diagnostic] = []

    def warn(self, code: str, message: str, pos: Optional[int] = None) -> None:
        self.items.append(Diagnostic("warning", code, message, pos))

    def note(self, code: str, message: str, pos: Optional[int] = None) -> None:
        self.items.append(Diagnostic("note", code, message, pos))

    def extend(self, other: "Diagnostics") -> None:
        self.items.extend(other.items)

    def __iter__(self):
        return iter(self.items)