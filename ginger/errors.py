from dataclasses import dataclass

@dataclass
class TypecheckError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message

@dataclass
class EvalError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message