from dataclasses import dataclass, field
from typing import List, Union


# =====================
# AST
# =====================

@dataclass(frozen=True)
class Program:
    items: List["TopLevel"]


TopLevel = Union[
    "GuaranteeDecl",
    "TypeGroupDecl",
    "RegisterDecl",
    "ImplDecl",
    "SigDecl",
    "FuncDecl",
    "VarDecl",
    "AssignStmt",
    "ExprStmt",
    "TryStmt",
    "CatchStmt",
]


# --- statements ---

Stmt = Union[
    "VarDecl", 
    "AssignStmt",
    "ExprStmt", 
    "TryStmt",
    "CatchStmt",
    "ReturnStmt", 
    ]

@dataclass(frozen=True)
class BlockStmt:
    stmts: List[Stmt]

@dataclass(frozen=True)
class ReturnStmt:
    expr: "Expr"

@dataclass(frozen=True)
class TryStmt:
    expr: "Expr"

@dataclass(frozen=True)
class CatchStmt:
    failure_name: str
    expr: "Expr"

@dataclass(frozen=True)
class ExprStmt:
    expr: "Expr"


# ---types ---

@dataclass(frozen=True)
class TypeRef:
    name: str  # Int, Float, String, Self, T, Number, etc.

@dataclass(frozen=True)
class Param:
    name: str
    typ: TypeRef


# --- guarantee/typegroup/register ---

@dataclass(frozen=True)
class FuncSig:
    name: str
    params: List[Param]
    ret: TypeRef
    # body: "Expr"
    attrs: List[str] = field(default_factory=list)

@dataclass(frozen=True)
class GuaranteeDecl:
    name: str
    methods: List[FuncSig]  # signatures inside guarantee

@dataclass(frozen=True)
class TypeGroupDecl:
    name: str
    members: List[TypeRef]  # Int | Float | ...

@dataclass(frozen=True)
class RegisterDecl:
    typ: TypeRef
    guarantee: str


# ---- impl (legacy: builtin mapping) ----

@dataclass(frozen=True)
class ImplMethod:
    name: str        # add
    builtin: str     # core.int.add

@dataclass(frozen=True)
class ImplDecl:
    typ: TypeRef
    guarantee: str
    methods: List[ImplMethod]


# ---- require clauses ----

RequireClause = Union["RequireIn", "RequireGuarantees"]

@dataclass(frozen=True)
class RequireIn:
    type_var: str     # T
    group_name: str   # Number

@dataclass(frozen=True)
class RequireGuarantees:
    type_var: str         # T
    guarantee_name: str   # Addable


# --- sig / func ---

@dataclass(frozen=True)
class SigDecl:
    name: str
    params: List[TypeRef]
    ret: TypeRef
    requires: List[RequireClause]
    failure: TypeRef
    attrs: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class FuncDecl:
    name: str
    params: List[Param]
    body: "BlockStmt"
    #ret: TypeRef
    #requires: List[RequireClause]
    #failure: TypeRef
    attrs: list[str] = field(default_factory=list)
    #origin: str = "unknown"     # "catalog" | "code" | "impl" | "unknown"


# ---- code (binding) ----

@dataclass(frozen=True)
class VarDecl:
    mutable: bool
    typ: TypeRef
    name: str
    expr: "Expr"

@dataclass(frozen=True)
class AssignStmt:
    name: str
    expr: "Expr"


# ---- expressions ----

Expr = Union[
    "CallExpr", 
    "IdentExpr", 
    "IntLit", 
    "FloatLit", 
    "BinaryExpr",
    ]

@dataclass(frozen=True)
class IdentExpr:
    name: str

@dataclass(frozen=True)
class IntLit:
    value: int

@dataclass(frozen=True)
class FloatLit:
    value: float

@dataclass(frozen=True)
class BinaryExpr:
    op: str     # { + | - | * | / }
    left: Expr
    right: Expr

@dataclass(frozen=True)
class PosArg:
    expr: Expr

@dataclass(frozen=True)
class NamedArg:
    name: str
    expr: Expr

Arg = Union[PosArg, NamedArg]

@dataclass(frozen=True)
class CallExpr:
    callee: str
    args: List[Arg]
    arg_style: str  # "pos" or "named"