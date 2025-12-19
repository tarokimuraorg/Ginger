from dataclasses import dataclass

@dataclass
class GingerError(Exception):
    """Base class for all Ginger errors"""
    pass

@dataclass
class RuntimeError(Exception):
    pass

@dataclass
class TypecheckError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message

@dataclass
class ParseError(Exception):
    pass

@dataclass
class EvalError(Exception):
    message: str
    def __str__(self) -> str:
        return self.message