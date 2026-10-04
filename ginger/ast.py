from dataclasses import dataclass, field
from typing import List, Union, Tuple, TYPE_CHECKING
from ginger.core.failure_spec import FailureSet

if TYPE_CHECKING:
    from ginger.core.failure_contract import ConditionalFailure

# =====================
# AST
# =====================

@dataclass(frozen=True)
class Program:
    items: List["TopLevel"]


TopLevel = Union[
    "FailureSetDecl",
    "GuaranteeDecl",
    "TypeGroupDecl",
    "RegisterDecl",
    "ImplDecl",
    "SigDecl",
    "FuncDecl",
    "VarDecl",
    "AssignStmt",
    "ExprStmt",
    "ResolveStmt",
]


# --- statements ---

Stmt = Union[
    "VarDecl", 
    "AssignStmt",
    "ExprStmt", 
    "ReturnStmt", 
    ]

@dataclass(frozen=True)
class BlockStmt:
    stmts: List[Stmt]

@dataclass(frozen=True)
class ReturnStmt:
    expr: "Expr"

@dataclass(frozen=True)
class ResolveHandler:
    failure_name: str
    body: BlockStmt

@dataclass(frozen=True)
class ResolveStmt:
    target: str
    handlers: List[ResolveHandler]

@dataclass(frozen=True)
class ExprStmt:
    expr: "Expr"


# ---types ---

@dataclass(frozen=True)
class TypeRef:
    name: str  # Int, Float, String, Self, T, Number, etc.
    args: Tuple["TypeRef",...] = ()
    # Source names are resolved before type checking; args contain only result types.
    failure_specs: Tuple[str, ...] | None = None
    latent_failures: FailureSet | None = None
    latent_conditions: frozenset["ConditionalFailure"] = frozenset()

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
    attrs: List[str] = field(default_factory=list)

@dataclass(frozen=True)
class FailureSetDecl:
    name: str
    members: List[str]  # Preserve duplicates for symbol validation.


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


# ---- impl (builtin mapping) ----

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
class ConditionalFailureDecl:
    failure_name: str
    type_var: str
    guarantee_name: str


@dataclass(frozen=True)
class SigDecl:
    name: str
    params: List[TypeRef]
    ret: TypeRef
    requires: List[RequireClause]
    failures: list[str | ConditionalFailureDecl] = field(default_factory=list)
    attrs: list[str] = field(default_factory=list)
    builtin: str | None = None

@dataclass(frozen=True)
class FuncDecl:
    name: str
    params: List[Param]
    body: BlockStmt
    attrs: list[str] = field(default_factory=list)


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
    "BinaryExpr",
    "UnaryMinusExpr",
    "CallExpr", 
    "IdentExpr", 
    "IntLit",
    "Int64Lit",
    "FloatLit", 
    ]

@dataclass(frozen=True)
class IdentExpr:
    name: str

@dataclass(frozen=True)
class IntLit:
    value: int

@dataclass(frozen=True)
class Int64Lit:
    value: int

@dataclass(frozen=True)
class FloatLit:
    value: float

@dataclass(frozen=True)
class UnaryMinusExpr:
    operand: Expr


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
